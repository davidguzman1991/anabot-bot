
import os
import json
import time
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta, timezone

import requests
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

# -------------------------------------------------------------
# App & Logging
# -------------------------------------------------------------
app = FastAPI(title="Ana Chatbot")
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("ana")

# -------------------------------------------------------------
# Environment
# -------------------------------------------------------------
WHATSAPP_TOKEN: str = os.getenv("WHATSAPP_TOKEN", "").strip()
WHATSAPP_VERIFY_TOKEN: str = os.getenv("WHATSAPP_VERIFY_TOKEN", "ANA_CHATBOT").strip()
WHATSAPP_PHONE_ID: str = os.getenv("WHATSAPP_PHONE_ID", "").strip()

TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "").strip()

# Optional config
GOOGLE_CALENDAR_ID: str = os.getenv("GOOGLE_CALENDAR_ID", "primary").strip()
TZ: str = os.getenv("TZ", "America/Guayaquil").strip()
DEFAULT_APPT_MIN: int = int(os.getenv("DEFAULT_APPT_MIN", "45"))

# -------------------------------------------------------------
# In-memory state
# -------------------------------------------------------------
# Simple in-memory sessions: phone -> list of message dicts & last_seen
SESSIONS: Dict[str, Dict[str, Any]] = {}
# Simple template store
TEMPLATES: Dict[str, str] = {
    "saludo": "¡Hola! Soy ANA, asistente virtual. ¿En qué puedo ayudarle?",
    "info": "Puedo orientarle con información y coordinarle una valoración cuando lo necesite.",
    "precio": "La consulta tiene un costo de $XX. ¿Desea agendar o recibir más detalles?",
    "ubicacion": "Estamos en [Tu dirección]. ¿Le comparto el pin de ubicación?",
}

# -------------------------------------------------------------
# Helpers
# -------------------------------------------------------------
def now_utc() -> datetime:
    return datetime.now(timezone.utc)

def safe_int(x: str, default: int = 0) -> int:
    try:
        return int(x)
    except Exception:
        return default

def notify_telegram(text: str) -> bool:
    """
    Send a simple text message to configured TELEGRAM_CHAT_ID.
    """
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return False
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": int(TELEGRAM_CHAT_ID),
            "text": text,
            "parse_mode": "Markdown"
        }
        r = requests.post(url, json=payload, timeout=10)
        if not r.ok:
            log.warning("Telegram send failed: %s %s", r.status_code, r.text)
        return r.ok
    except Exception as e:
        log.exception("Error sending to Telegram: %s", e)
        return False

def notify_whatsapp(phone: str, message: str) -> bool:
    """
    Send a WhatsApp text message using Cloud API.
    Phone should be in E.164 (e.g. +593...); we add '+' if missing.
    """
    if not WHATSAPP_TOKEN or not WHATSAPP_PHONE_ID or not phone:
        return False
    try:
        if not phone.startswith("+"):
            phone = f"+{phone}"
        url = f"https://graph.facebook.com/v17.0/{WHATSAPP_PHONE_ID}/messages"
        headers = {
            "Authorization": f"Bearer {WHATSAPP_TOKEN}",
            "Content-Type": "application/json"
        }
        payload = {
            "messaging_product": "whatsapp",
            "to": phone,
            "type": "text",
            "text": {"body": message}
        }
        r = requests.post(url, headers=headers, json=payload, timeout=10)
        if not r.ok:
            log.warning("WA send failed: %s %s", r.status_code, r.text)
        return r.ok
    except Exception as e:
        log.exception("Error sending to WhatsApp: %s", e)
        return False

def touch_session(phone: str, role: str, content: str) -> None:
    """
    Store message in session history (last 24h kept in memory).
    """
    sess = SESSIONS.get(phone)
    if not sess:
        sess = {"history": [], "last_seen": now_utc().isoformat()}
        SESSIONS[phone] = sess
    sess["last_seen"] = now_utc().isoformat()
    sess["history"].append({
        "ts": now_utc().isoformat(),
        "role": role,
        "content": content,
    })
    # prevent unbounded growth
    if len(sess["history"]) > 200:
        sess["history"] = sess["history"][-200:]

def list_active_sessions(hours: int = 24) -> List[str]:
    cutoff = now_utc() - timedelta(hours=hours)
    active = []
    for phone, sess in SESSIONS.items():
        try:
            last = datetime.fromisoformat(sess.get("last_seen"))
        except Exception:
            last = now_utc() - timedelta(days=365)
        if last >= cutoff:
            active.append(phone)
    return sorted(active)

# -------------------------------------------------------------
# Health/Root
# -------------------------------------------------------------
@app.get("/health")
async def health() -> Dict[str, Any]:
    return {
        "status": "ok",
        "calendar_id": GOOGLE_CALENDAR_ID,
        "tz": TZ,
        "duration_min": DEFAULT_APPT_MIN,
        "scheduler": True,
        "telegram_ready": bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID),
        "whatsapp_ready": bool(WHATSAPP_TOKEN and WHATSAPP_PHONE_ID),
    }

@app.get("/")
async def root() -> Dict[str, Any]:
    return {"status": "ok", "message": "Ana Chatbot running"}

# -------------------------------------------------------------
# WhatsApp Webhook (GET verify / POST receive)
# -------------------------------------------------------------
@app.get("/webhook")
async def whatsapp_verify(request: Request):
    """
    WhatsApp webhook verification (Meta will call this)
    """
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")

    if mode == "subscribe" and token == WHATSAPP_VERIFY_TOKEN and challenge is not None:
        # Return the challenge as plain text
        return Response(content=str(challenge), media_type="text/plain")
    return JSONResponse(content={"detail": "Verification failed"}, status_code=403)

@app.post("/webhook")
async def whatsapp_receive(request: Request):
    """
    WhatsApp incoming messages handler.
    Always return 200 to avoid retries, and do our processing best-effort.
    """
    try:
        body = await request.json()
        log.debug("WA webhook body: %s", json.dumps(body))

        entry = (body.get("entry") or [{}])[0]
        changes = (entry.get("changes") or [{}])[0]
        value = changes.get("value", {})
        messages = value.get("messages") or []

        if messages:
            msg = messages[0]
            wa_from = msg.get("from")  # E164 without '+'
            incoming_text = (msg.get("text") or {}).get("body", "").strip()

            # store inbound
            touch_session(wa_from, "user", incoming_text)

            # Compose ANA reply (basic triage for now)
            reply_text = "Gracias por su mensaje. Puedo orientarle con información y ayudarle a coordinar una valoración cuando lo necesite. ¿Qué le gustaría consultar?"

            # naive rules based on templates
            low = incoming_text.lower()
            if "precio" in low or "costo" in low:
                reply_text = TEMPLATES.get("precio", reply_text)
            elif "ubicación" in low or "direccion" in low or "dirección" in low:
                reply_text = TEMPLATES.get("ubicacion", reply_text)
            elif "hola" in low or "buenas" in low:
                reply_text = TEMPLATES.get("saludo", reply_text)

            # send reply
            notify_whatsapp(wa_from, reply_text)

            # store outbound
            touch_session(wa_from, "assistant", reply_text)

            # operator console notifications
            notify_telegram(f"📩 WhatsApp de {wa_from}: {incoming_text}")
            notify_telegram(f"🤖 ANA respondió a {wa_from}: {reply_text}")

    except Exception as e:
        log.exception("Error processing WA webhook: %s", e)

    # Always 200 OK for Meta
    return JSONResponse(content={"status": "ok"})

# -------------------------------------------------------------
# Telegram: Operator console webhook
# -------------------------------------------------------------
def _tg_reply(chat_id: int, text: str) -> None:
    if not TELEGRAM_BOT_TOKEN:
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
        requests.post(url, json=payload, timeout=10)
    except Exception:
        pass

@app.post("/telegram")
async def telegram_webhook(request: Request):
    """
    Telegram webhook for operator console.
    Commands:
      /who                  -> lista sesiones activas (24h)
      /hist <n> [k]         -> historial de la sesión; k=cuántos mensajes (default 10)
      /tpl <NOMBRE> <TEXTO> -> guarda/actualiza plantilla
      /r <n> <mensaje>      -> responde al número por WhatsApp con <mensaje>
    """
    try:
        update = await request.json()
        message = update.get("message") or {}
        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        text = (message.get("text") or "").strip()

        # Optional gate: only accept messages from configured chat id
        if TELEGRAM_CHAT_ID and str(chat_id) != str(TELEGRAM_CHAT_ID):
            # ignore silently
            return JSONResponse(content={"ok": True})

        if not text:
            _tg_reply(chat_id, "Mensaje vacío.")
            return JSONResponse(content={"ok": True})

        if text.startswith("/who"):
            actives = list_active_sessions(24)
            if not actives:
                _tg_reply(chat_id, "No hay sesiones activas en las últimas 24 h.")
            else:
                _tg_reply(chat_id, "Sesiones activas (24h):\n• " + "\n• ".join(actives))

        elif text.startswith("/hist"):
            # /hist <numero> [k]
            parts = text.split(maxsplit=2)
            if len(parts) >= 2:
                phone = parts[1]
                k = 10
                if len(parts) == 3:
                    k = safe_int(parts[2], 10)
                sess = SESSIONS.get(phone)
                if not sess:
                    _tg_reply(chat_id, f"No hay historial para {phone}.")
                else:
                    hist = sess.get("history", [])[-k:]
                    if not hist:
                        _tg_reply(chat_id, f"Historial vacío para {phone}.")
                    else:
                        lines = []
                        for item in hist:
                            role = item.get("role", "?")
                            content = item.get("content", "")
                            lines.append(f"*{role}*: {content}")
                        _tg_reply(chat_id, "\n".join(lines))
            else:
                _tg_reply(chat_id, "Uso: /hist <numero> [k]")

        elif text.startswith("/tpl"):
            # /tpl <NOMBRE> <TEXTO>
            parts = text.split(maxsplit=2)
            if len(parts) < 3:
                _tg_reply(chat_id, "Uso: /tpl <NOMBRE> <TEXTO>")
            else:
                name = parts[1].lower()
                value = parts[2]
                TEMPLATES[name] = value
                _tg_reply(chat_id, f"Plantilla '{name}' guardada.")

        elif text.startswith("/r"):
            # /r <numero> <mensaje>
            parts = text.split(maxsplit=2)
            if len(parts) < 3:
                _tg_reply(chat_id, "Uso: /r <numero> <mensaje>")
            else:
                phone = parts[1]
                msg = parts[2]
                ok = notify_whatsapp(phone, msg)
                if ok:
                    touch_session(phone, "assistant", f"[OP] {msg}")
                    _tg_reply(chat_id, f"Enviado a {phone}.")
                else:
                    _tg_reply(chat_id, f"No se pudo enviar a {phone}. Revise logs.")

        else:
            # help
            help_text = (
                "Comandos:\n"
                "/who — lista sesiones activas (24h)\n"
                "/hist <numero> [n] — historial de la sesión\n"
                "/tpl <NOMBRE> <TEXTO> — simula plantilla/guarda\n"
                "/r <numero> <mensaje> — responde por WhatsApp\n"
            )
            _tg_reply(chat_id, help_text)

    except Exception as e:
        log.exception("Error processing TG webhook: %s", e)

    # Telegram needs a 200 OK quickly
    return JSONResponse(content={"ok": True})

# -------------------------------------------------------------
# Alternate WhatsApp path (no-op, for sanity)
# -------------------------------------------------------------
@app.post("/whatsapp/webhook")
async def whatsapp_webhook_receive_alt(request: Request):
    # Meta expects 200 OK; keep it minimal
    try:
        _ = await request.json()
    except Exception:
        pass
    return JSONResponse(content={"status": "ok"})
