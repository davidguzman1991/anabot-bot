# main.py
from __future__ import annotations
import logging
import os
from typing import Any, Dict, Optional

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from config import (
    WHATSAPP_TOKEN,
    WHATSAPP_PHONE_NUMBER_ID,
    WHATSAPP_VERIFY_TOKEN,
    FLOW_JSON_PATH,
    config_debug_snapshot,
)
from flow_engine import FlowEngine

logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
log = logging.getLogger("anabot")

app = FastAPI(title="AnaBot")

# Motor y "sesiones" en memoria (puedes cambiarlo por tu SessionStore)
engine = FlowEngine(FLOW_JSON_PATH)
SESS: Dict[str, str] = {}  # wa_id -> current_node_id


# --------------------------- Salud -------------------------------
@app.get("/salud")
def salud():
    return {"ok": True, "cfg": config_debug_snapshot()}


# ----------------------- Webhook Verify --------------------------
@app.get("/webhook/whatsapp")
async def wa_verify(request: Request):
    # Meta/WhatsApp verifica con hub.mode, hub.challenge, hub.verify_token
    params = dict(request.query_params)
    mode = params.get("hub.mode")
    token = params.get("hub.verify_token")
    challenge = params.get("hub.challenge")

    if mode == "subscribe" and token == WHATSAPP_VERIFY_TOKEN:
        log.info("Webhook verificado OK")
        return PlainTextResponse(content=challenge or "", status_code=200)
    log.warning("Verificación webhook FALLÓ")
    return PlainTextResponse(content="forbidden", status_code=403)


# ----------------------- Webhook Receiver ------------------------
@app.post("/webhook/whatsapp")
async def wa_webhook(request: Request):
    body = await request.json()
    log.info("webhook WA IN: %s", body)

    # Extraer mensaje de texto (simplificado)
    try:
        entry = body["entry"][0]
        changes = entry["changes"][0]
        value = changes["value"]
        messages = value.get("messages") or []
        if not messages:
            return JSONResponse({"status": "ignored"}, 200)

        msg = messages[0]
        wa_from = msg.get("from")
        msg_type = msg.get("type")
        text_in = ""
        if msg_type == "text":
            text_in = (msg.get("text") or {}).get("body", "")
        else:
            text_in = ""

    except Exception as e:
        log.exception("payload no esperado: %s", e)
        return JSONResponse({"status": "bad_payload"}, 200)

    # Estado actual
    current_state: Optional[str] = SESS.get(wa_from)

    # Ejecutar motor
    out = engine.run(text_in, current_state)
    try:
        log.info("FLOW OUT DEBUG: reply_len=%s next=%s",
                 len((out or {}).get("reply") or []),
                 (out or {}).get("next"))
    except Exception:
        log.exception("FLOW OUT DEBUG error")

    # Anti-eco: si no hay salida válida, forzar menú
    if not out or not out.get("reply") or not out.get("next"):
        log.warning("FLOW fallback → forzando menu_principal")
        out = engine.run("9", None)

    reply_lines = out.get("reply") or []
    next_state = out.get("next") or "menu_principal"
    SESS[wa_from] = next_state

    reply_text = "\n".join(reply_lines) if isinstance(reply_lines, list) else str(reply_lines)

    # Enviar por WhatsApp
    await send_wa_text(wa_from, reply_text)

    return JSONResponse({"status": "ok"}, 200)


# ----------------------- WhatsApp Sender -------------------------
async def send_wa_text(to_number: str, text: str) -> None:
    if not (WHATSAPP_TOKEN and WHATSAPP_PHONE_NUMBER_ID):
        log.warning("WA creds faltantes: no se envía mensaje")
        return

    url = f"https://graph.facebook.com/v20.0/{WHATSAPP_PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}"}
    payload: Dict[str, Any] = {
        "messaging_product": "whatsapp",
        "to": to_number,
        "type": "text",
        "text": {"preview_url": False, "body": text or " "}
    }
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(url, headers=headers, json=payload)
            log.info("WA send → %s %s", r.status_code, r.text[:200])
    except Exception:
        log.exception("WA send error")

