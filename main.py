# main.py — anabot-bot (listo para producción)
import os
import json
import logging
from datetime import datetime, timezone

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, PlainTextResponse

import httpx

# ---------- LOGGING ----------
logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
log = logging.getLogger("anabot")

# ---------- ENV ----------
VERIFY_TOKEN = (
    os.getenv("WHATSAPP_VERIFY_TOKEN")
    or os.getenv("WA_VERIFY_TOKEN")
    or os.getenv("VERIFY_TOKEN")
)

PHONE_NUMBER_ID = (
    os.getenv("WHATSAPP_PHONE_NUMBER_ID")
    or os.getenv("WA_PHONE_ID")
    or os.getenv("PHONE_NUMBER_ID")
)

WHATSAPP_TOKEN = (
    os.getenv("WHATSAPP_TOKEN")
    or os.getenv("WA_TOKEN")
    or os.getenv("TOKEN")
)

GRAPH_URL = (
    f"https://graph.facebook.com/v20.0/{PHONE_NUMBER_ID}/messages"
    if PHONE_NUMBER_ID else None
)

app = FastAPI(title="AnaBot", version="1.0")

# ---------- HEALTH ----------
@app.get("/")
def root():
    return {"ok": True, "service": "anabot", "env": "/etc/profile"}

@app.get("/health")
def health():
    return {"ok": True}

@app.get("/health/db")
def health_db():
    # Ejemplo simple para ver columnas esperadas de 'sessions'
    return {
        "ok": True,
        "db": "postgres",
        "sessions_columns": [
            "id", "user_id", "platform", "last_activity_ts", "has_greeted",
            "current_state", "status", "extra", "canal", "user_key",
        ],
    }

# ---------- WHATSAPP SEND ----------
async def wa_send_text(to: str, text: str) -> tuple[int, dict]:
    if not PHONE_NUMBER_ID or not WHATSAPP_TOKEN:
        log.error("Faltan variables de entorno para WhatsApp: PHONE_NUMBER_ID o WHATSAPP_TOKEN")
        return 0, {"error": "missing env"}

    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": text},
    }

    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post(GRAPH_URL, headers=headers, json=payload)

    try:
        body = r.json()
    except Exception:
        body = {"raw": r.text}

    log.info("WA send → %s %s", r.status_code, body)
    return r.status_code, body

# ---------- WEBHOOK VERIFY ----------
@app.get("/webhook/whatsapp")
def wa_verify(
    mode: str | None = None,
    hub_mode: str | None = None,
    hub_challenge: str | None = None,
    hub_verify_token: str | None = None,
    hub_topic: str | None = None,
):
    # Meta envía params como hub.mode, hub.verify_token, hub.challenge
    mode = mode or hub_mode  # por si algún proxy renombra
    token = hub_verify_token
    challenge = hub_challenge

    if mode == "subscribe" and token and VERIFY_TOKEN and token == VERIFY_TOKEN:
        log.info("Webhook verificado OK")
        return PlainTextResponse(challenge or "", status_code=200)

    log.warning(
        "Fallo verificación webhook: mode=%s token_ok=%s",
        mode,
        bool(token and VERIFY_TOKEN and token == VERIFY_TOKEN),
    )
    return Response(status_code=403)

# ---------- WEBHOOK INCOMING ----------
@app.post("/webhook/whatsapp")
async def wa_webhook(req: Request) -> Response:
    try:
        body = await req.json()
    except Exception:
        body = {}

    log.info("WA webhook IN: %s", json.dumps(body, ensure_ascii=False))

    # Estructura típica de WhatsApp Cloud
    entries = body.get("entry", [])
    for entry in entries:
        changes = entry.get("changes", [])
        for ch in changes:
            value = ch.get("value", {})
            messages = value.get("messages", [])
            for msg in messages:
                wa_from = msg.get("from")       # número E.164 sin '+'
                mtype   = msg.get("type")
                text_in = ""
                if mtype == "text":
                    text_in = (msg.get("text") or {}).get("body", "").strip()

                log.info("IN msg: from=%s type=%s text=%r", wa_from, mtype, text_in)

                # ---- LÓGICA SIMPLE DE RESPUESTA ----
                if text_in.lower() in ("hora", "hora?"):
                    now = datetime.now(timezone.utc).astimezone()
                    reply = f"⏰ Son las {now.strftime('%H:%M:%S')} ({now.tzinfo})"
                elif text_in:
                    reply = f"👋 Hola! Recibí: {text_in}"
                else:
                    reply = "Estoy procesando tu mensaje. Intenta con texto."

                if wa_from:
                    await wa_send_text(wa_from, reply)

    return JSONResponse({"ok": True})
