import os, re, json, logging, unicodedata, threading, time
from typing import Optional, Dict, Any, List
from datetime import datetime, timedelta

import requests
from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

try:
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.interval import IntervalTrigger
    APSCHED_AVAILABLE = True
except Exception:
    APSCHED_AVAILABLE = False

from flow_engine import FlowEngine
from hooks import DB  # in-memory demo DB (replace with Postgres later)

# ---------------------------
# Logging (structured & safe)
# ---------------------------
logger = logging.getLogger("chatbot_ana")
handler = logging.StreamHandler()
formatter = logging.Formatter(
    fmt='%(asctime)s | %(levelname)s | %(name)s | %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
handler.setFormatter(formatter)
logger.addHandler(handler)
logger.setLevel(logging.INFO)

# ---------------
# Env config
# ---------------
ALLOWED_ORIGINS = [o.strip() for o in os.getenv("ALLOWED_ORIGINS", "").split(",") if o.strip()]
WA_VERIFY_TOKEN = os.getenv("WA_VERIFY_TOKEN", "")
WA_TOKEN = os.getenv("WA_TOKEN", "")
WA_PHONE_ID = os.getenv("WA_PHONE_ID", "")
WA_GRAPH_VERSION = os.getenv("WA_GRAPH_VERSION", "v19.0")

TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN", "")  # optional

# ---------------------------
# App & CORS
# ---------------------------
app = FastAPI(title="Chat Bot Ana API", version="1.0.0")

if ALLOWED_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=ALLOWED_ORIGINS,
        allow_credentials=True,
        allow_methods=["POST", "GET", "OPTIONS"],
        allow_headers=["Content-Type", "Authorization"],
    )

# ---------------------------
# Flow Engine
# ---------------------------
engine = FlowEngine(flow_path="flow.json")
INACTIVITY_FIRST_DELTA = timedelta(minutes=15)
INACTIVITY_FINAL_DELTA = timedelta(minutes=30)

# ---------------------------
# Helpers for session metadata
# ---------------------------
def update_session_meta(session_id: str, channel: str, destination: str):
    st = engine.store.get(session_id)
    meta = st.setdefault("ctx", {}).setdefault("_meta", {})
    meta["channel"] = channel
    meta["destination"] = str(destination)
    engine.store.set(session_id, st)


def send_inactivity_message(channel: str, destination: str, stage: int):
    if stage == 1:
        message = (
            "¿Seguimos con tu trámite? Cuando estés listo vuelve a indicarme el número "
            "de la opción que prefieras para continuar."
        )
    else:
        message = (
            "Cierro la conversación por inactividad. Cuando desees retomar, escríbeme "
            "y con gusto te ayudo a coordinar una consulta con el Dr. Guzmán."
        )
    try:
        if channel == "whatsapp":
            wa_send_text(destination, message)
        elif channel == "telegram":
            tg_send_text(int(destination), message)
    except Exception as exc:
        logger.exception(f"Inactivity message failed ({channel}): {exc}")


def check_inactive_sessions():
    now = datetime.utcnow()
    sessions_snapshot = list(engine.store.sessions.items())
    for sid, st in sessions_snapshot:
        last_iso = st.get("last_activity")
        if not last_iso:
            continue
        try:
            last_dt = datetime.fromisoformat(last_iso)
        except ValueError:
            continue
        diff = now - last_dt
        stage = st.get("inactivity_stage", 0)
        meta = st.get("ctx", {}).get("_meta", {})
        channel = meta.get("channel")
        destination = meta.get("destination")
        if not channel or not destination:
            continue
        if stage < 1 and diff >= INACTIVITY_FIRST_DELTA:
            send_inactivity_message(channel, destination, stage=1)
            st["inactivity_stage"] = 1
            engine.store.set(sid, st)
            logger.info(f"Inactivity reminder sent to {sid}")
        elif stage < 2 and diff >= INACTIVITY_FINAL_DELTA:
            send_inactivity_message(channel, destination, stage=2)
            st["inactivity_stage"] = 2
            meta["conversation_closed"] = True
            engine.store.set(sid, st)
            logger.info(f"Inactivity closure sent to {sid}")


# ---------------------------
# Small NLP: map free text -> menu key
# (B1/C2: normalización y sinónimos)
# ---------------------------
def norm(s: str) -> str:
    s = s or ""
    s = s.lower().strip()
    s = "".join(ch for ch in unicodedata.normalize("NFD", s) if unicodedata.category(ch) != "Mn")
    s = re.sub(r"\s+", " ", s)
    return s

# Regex con límites de palabra
PATTERNS: List[tuple] = [
    (re.compile(r"\b(cita|agendar|agenda|reservar|turno)\b"), "1"),         # Agendar
    (re.compile(r"\b(servicios?|informacion|info)\b"), "2"),                # Más info de servicios
    (re.compile(r"\b(precio|tarifa|cuanto vale|cuanto cuesta|valor)\b"), "3"),  # Precios
    (re.compile(r"\b(ubicacion|direccion|donde|mapa)\b"), "4"),             # Ubicaciones
    (re.compile(r"\b(reagendar|cambiar|modificar|mover|posponer|cancelar)\b"), "5"), # Reagendar/Cancelar
    (re.compile(r"\b(portal)\b"), "6"),
    (re.compile(r"\b(contacto|contactar|humano|doctor|dr|atender)\b"), "7"),
]


def map_text_to_key(text: str) -> str:
    t = norm(text)
    for pat, key in PATTERNS:
        if pat.search(t):
            return key
    # deja el texto original si no mapeó
    return text.strip()

# ---------------------------
# WhatsApp helpers
# ---------------------------
def wa_send_text(to_number: str, text: str) -> Optional[Dict[str, Any]]:
    if not (WA_TOKEN and WA_PHONE_ID):
        logger.warning("WA credentials missing, skip send")
        return None
    url = f"https://graph.facebook.com/{WA_GRAPH_VERSION}/{WA_PHONE_ID}/messages"
    headers = {"Authorization": f"Bearer {WA_TOKEN}", "Content-Type": "application/json"}
    payload = {
        "messaging_product": "whatsapp",
        "to": to_number,
        "type": "text",
        "text": {"body": text}
    }
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=10)
        if resp.status_code >= 400:
            logger.error(f"WA send error: {resp.status_code} {resp.text}")
        return resp.json()
    except Exception as e:
        logger.exception(f"WA send exception: {e}")
        return None


def wa_extract_text(msg: Dict[str, Any]) -> str:
    mtype = msg.get("type")
    if mtype == "text":
        return (msg.get("text", {}) or {}).get("body", "") or ""
    elif mtype == "interactive":
        it = msg.get("interactive", {}) or {}
        btn = it.get("button_reply") or {}
        lst = it.get("list_reply") or {}
        return btn.get("title") or lst.get("title") or ""
    else:
        return ""  # no-text


def format_engine_reply(out: Dict[str, Any]) -> str:
    # Combina el mensaje + opciones (si las hay)
    message = out.get("message", "").strip()
    options = out.get("options", [])
    if options:
        message += "\n\n" + "\n".join(f"- {opt}" for opt in options)
    return message

# ---------------------------
# Telegram helpers (opcional)
# ---------------------------
def tg_send_text(chat_id: int, text: str) -> Optional[Dict[str, Any]]:
    if not TG_BOT_TOKEN:
        logger.warning("TG token missing, skip send")
        return None
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    try:
        resp = requests.post(url, json={"chat_id": chat_id, "text": text}, timeout=10)
        if resp.status_code >= 400:
            logger.error(f"TG send error: {resp.status_code} {resp.text}")
        return resp.json()
    except Exception as e:
        logger.exception(f"TG send exception: {e}")
        return None

# ---------------------------
# Scheduler (A2/E1): recordatorios 24h/2h
# Nota: ejemplo en memoria. Reemplaza por consultas a tu BD.
# ---------------------------
_REMIND_SENT: Dict[str, set] = {"24h": set(), "2h": set()}


def _send_reminder(ap: Dict[str, Any], horizon: str):
    # Intentamos WA si tenemos teléfono del paciente
    cedula = ap.get("cedula")
    # Buscar el paciente en memoria (hooks.DB); en prod: SELECT
    patient = DB["patients"].get(cedula, {})
    telefono = patient.get("telefono")
    if telefono:
        msg = f"Recordatorio: tienes tu cita el {ap['inicio'].strftime('%d-%m-%Y %H:%M')} en {ap['sede']}."
        wa_send_text(telefono, msg)


def check_reminders():
    now = datetime.now()
    for ap in DB["appointments"]:
        if ap.get("estado") != "confirmada":
            continue
        start = ap["inicio"]
        # 24 horas
        delta24 = abs((start - now).total_seconds() - 24*3600)
        if delta24 <= 300:  # 5 min window
            key = f"{ap['id']}"
            if key not in _REMIND_SENT["24h"]:
                _send_reminder(ap, "24h")
                _REMIND_SENT["24h"].add(key)
        # 2 horas
        delta2 = abs((start - now).total_seconds() - 2*3600)
        if delta2 <= 300:
            key = f"{ap['id']}"
            if key not in _REMIND_SENT["2h"]:
                _send_reminder(ap, "2h")
                _REMIND_SENT["2h"].add(key)


if APSCHED_AVAILABLE:
    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.add_job(check_reminders, IntervalTrigger(minutes=5))
    scheduler.add_job(check_inactive_sessions, IntervalTrigger(minutes=1))
    scheduler.start()
else:
    logger.warning("APScheduler not installed; background jobs fallback to thread loop.")

    def _background_loop():
        while True:
            try:
                check_reminders()
                check_inactive_sessions()
            except Exception:
                logger.exception("Background loop error")
            time.sleep(60)

    threading.Thread(target=_background_loop, daemon=True).start()

# ---------------------------
# Models for /chat
# ---------------------------
class ChatIn(BaseModel):
    session_id: str
    text: str

# ---------------------------
# Routes
# ---------------------------
@app.get("/health")
def health():
    return {"status": "ok", "time": datetime.utcnow().isoformat() + "Z"}


@app.post("/chat")
def chat(inp: ChatIn):
    # Guardar último texto para el escaneo de red flags
    st = engine.store.get(inp.session_id)
    st["ctx"]["last_text"] = inp.text
    engine.store.set(inp.session_id, st)

    mapped = map_text_to_key(inp.text)

    out = engine.process(inp.session_id, mapped)
    return out

# ---------------------------
# WhatsApp Webhook (Cloud API)
# ---------------------------
@app.get("/wa/webhook")
def wa_verify(request: Request):
    # Meta/Facebook sends GET with hub params
    params = dict(request.query_params)
    mode = params.get("hub.mode")
    token = params.get("hub.verify_token")
    challenge = params.get("hub.challenge")
    if mode == "subscribe" and token == WA_VERIFY_TOKEN:
        return int(challenge or 0)
    raise HTTPException(status_code=403, detail="Forbidden")


@app.post("/wa/webhook")
async def wa_webhook(req: Request):
    payload = await req.json()
    logger.info(f"WA inbound: {json.dumps(payload)[:500]}")
    try:
        entry = (payload.get("entry") or [])[0]
        changes = (entry.get("changes") or [])[0]
        value = changes.get("value", {})
        messages = value.get("messages", [])
        if not messages:
            return {"status": "ok"}  # acks like status updates

        for m in messages:
            from_id = m.get("from")  # number
            mtype = m.get("type")
            text = wa_extract_text(m)
            if not text:
                # Responder cortésmente a contenidos no texto
                wa_send_text(from_id, "Por ahora solo puedo leer texto. ¿Podrías escribirlo?")
                continue

            session_id = f"wa:{from_id}"
            update_session_meta(session_id, "whatsapp", from_id)

            st = engine.store.get(session_id)
            st["ctx"]["last_text"] = text
            engine.store.set(session_id, st)

            mapped = map_text_to_key(text)
            out = engine.process(session_id, mapped)
            reply = format_engine_reply(out)

            wa_send_text(from_id, reply)

        return {"status": "ok"}
    except Exception as e:
        logger.exception(f"Webhook WA error: {e}")
        return {"status": "error"}

# ---------------------------
# Telegram Webhook (opcional)
# ---------------------------
@app.post("/tg/webhook")
async def tg_webhook(req: Request):
    if not TG_BOT_TOKEN:
        raise HTTPException(status_code=400, detail="TG_BOT_TOKEN not configured")
    payload = await req.json()
    logger.info(f"TG inbound: {json.dumps(payload)[:500]}")

    try:
        if "message" in payload:
            msg = payload["message"]
            chat_id = msg["chat"]["id"]
            text = msg.get("text") or ""
            if not text:
                tg_send_text(chat_id, "Por ahora solo puedo leer texto. ¿Podrías escribirlo?")
                return {"status": "ok"}

            session_id = f"tg:{chat_id}"
            update_session_meta(session_id, "telegram", str(chat_id))
            st = engine.store.get(session_id)
            st["ctx"]["last_text"] = text
            engine.store.set(session_id, st)

            mapped = map_text_to_key(text)
            out = engine.process(session_id, mapped)
            reply = format_engine_reply(out)
            tg_send_text(chat_id, reply)

        elif "callback_query" in payload:
            cq = payload["callback_query"]
            chat_id = cq["message"]["chat"]["id"]
            data = cq.get("data") or ""
            session_id = f"tg:{chat_id}"
            update_session_meta(session_id, "telegram", str(chat_id))
            out = engine.process(session_id, data)
            reply = format_engine_reply(out)
            tg_send_text(chat_id, reply)

        return {"status": "ok"}
    except Exception as e:
        logger.exception(f"Webhook TG error: {e}")
        return {"status": "error"}
