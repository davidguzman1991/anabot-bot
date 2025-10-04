import os
import json
import logging
from datetime import datetime
from typing import Any, Dict, Optional

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

# --- motor de flujo y store de sesiones ---
from flow_engine import FlowEngine
from session_store import ensure_session_schema, get_session, upsert_session

# ------------------------------------------
# Config básica
# ------------------------------------------
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("anabot")

app = FastAPI(title="AnaBot")
engine = FlowEngine("flow.json")  # Tu lógica está en flow.json


# ------------------------------------------
# Helpers WhatsApp
# ------------------------------------------
def parse_wa_message(payload: Dict[str, Any]) -> Dict[str, Optional[str]]:
    """
    Extrae 'from', 'text' y 'phone_number_id' del payload de WhatsApp.
    Tolera estructuras ausentes y también eventos de 'statuses'.
    """
    # WA envuelve así: entry[0].changes[0].value
    val = (((payload.get("entry") or [{}])[0].get("changes") or [{}])[0].get("value") or {})

    # Si es status callback, no hay mensaje
    if "statuses" in val:
        return {"from": None, "text": None, "phone_number_id": (val.get("metadata") or {}).get("phone_number_id")}

    msg = (val.get("messages") or [{}])[0]
    wa_from = msg.get("from")
    text = (msg.get("text") or {}).get("body", "")
    phone_id = (val.get("metadata") or {}).get("phone_number_id")
    return {"from": wa_from, "text": text, "phone_number_id": phone_id}


async def wa_send_text(to: str, body: str, phone_number_id: Optional[str] = None) -> Dict[str, Any]:
    """
    Envía un texto por la API de WhatsApp Cloud.
    Usa phone_number_id del webhook o la variable WA_PHONE_ID.
    """
    token = os.getenv("WA_TOKEN", "").strip()
    phone_id = (phone_number_id or os.getenv("WA_PHONE_ID", "").strip())

    if not token or not phone_id:
        log.warning("WA_TOKEN o WA_PHONE_ID faltan; no se puede enviar.")
        return {"ok": False, "reason": "missing_token_or_phone_id"}

    url = f"https://graph.facebook.com/v20.0/{phone_id}/messages"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    data = {"messaging_product": "whatsapp", "to": to, "type": "text", "text": {"body": body}}

    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post(url, headers=headers, json=data)
        try:
            payload = r.json()
        except Exception:
            payload = {"raw": r.text}
        log.info("WA send → %s %s", r.status_code, payload)
        return {"status": r.status_code, "payload": payload}


# ------------------------------------------
# FastAPI lifecycle
# ------------------------------------------
@app.on_event("startup")
async def _on_startup():
    # Garantiza que la tabla 'sessions' exista
    ensure_session_schema()
    log.info("✅ DB schema verificado/creado")
    log.info("🧩 Flow cargado: nodes=%s edges=%s start=%s",
             len(engine.nodes), len(engine.edges), engine.start)


# ------------------------------------------
# Rutas básicas
# ------------------------------------------
@app.get("/")
async def root():
    return {"ok": True, "service": "anabot", "env": "/etc/profile"}

@app.get("/health")
async def health():
    return {"ok": True}

# /health/db es opcional; lo dejamos mínimo sin tocar tu session_store
@app.get("/health/db")
async def health_db():
    return {"ok": True, "db": "postgres", "sessions_columns": [
        "id", "user_id", "platform", "last_activity_ts",
        "has_greeted", "current_state", "status", "extra", "canal", "user_key"
    ]}


# ------------------------------------------
# Webhook de WhatsApp
# ------------------------------------------
@app.post("/webhook/whatsapp")
async def wa_webhook(req: Request):
    data = await req.json()
    log.info("WA webhook IN: %s", json.dumps(data, ensure_ascii=False))

    info = parse_wa_message(data)

    # Ignora callbacks de 'statuses' (entregas, leído, etc.)
    if not info.get("text") and info.get("from") is None:
        return JSONResponse({"ok": True})

    wa_from: Optional[str] = info.get("from")
    text_in: str = (info.get("text") or "").strip()
    phone_id: Optional[str] = info.get("phone_number_id")

    # Si por alguna razón no hay texto/usuario, salimos
    if not wa_from or not text_in:
        return JSONResponse({"ok": True})

    # Lee la sesión actual (state)
    sess = (get_session(wa_from, "whatsapp") or {})
    current_state = (sess.get("current_state") or None)

    # Procesa por el motor de flujo
    reply_text = ""
    next_state = current_state

    out = engine.run(text_in, current_state)
    if out:
        # engine.run devuelve {"reply": [..], "next": "id"}
        reply_text = "\n".join(out.get("reply") or [])
        next_state = out.get("next") or current_state
    else:
        # fallbacks mínimos (hora/eco)
        if text_in.lower() in ("hora", "hora?", "qué hora es", "que hora es"):
            reply_text = f"🕒 Son las {datetime.now().strftime('%H:%M:%S')}"
        else:
            reply_text = f"👋 Hola! Recibí: {text_in}"

    # Persiste el nuevo estado
    upsert_session(
        user_id=wa_from,
        platform="whatsapp",
        current_state=next_state,
        status="ok",
    )

    # Envía respuesta al usuario
    if reply_text:
        await wa_send_text(wa_from, reply_text, phone_number_id=phone_id)

    return JSONResponse({"ok": True})
