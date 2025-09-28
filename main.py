from __future__ import annotations

import logging
import os
import re
import unicodedata
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

import requests
from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from zoneinfo import ZoneInfo

from config import get_settings
from db import engine
from repo import create_appointment, get_patient_by_dni, upsert_patient
from utils.google_calendar import create_calendar_event

settings = get_settings()

logger = logging.getLogger("anabot")
logging.basicConfig(level=logging.INFO)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or settings.TELEGRAM_TOKEN
if not TELEGRAM_BOT_TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN/TELEGRAM_TOKEN env var is required")

PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "")
TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

APPT_DURATION_MIN = int(os.getenv("APPT_DURATION_MIN", "45"))
TZ = ZoneInfo("America/Guayaquil")

app = FastAPI(title="AnaBot", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_headers=["*"],
    allow_methods=["*"],
)


@app.on_event("startup")
async def log_routes() -> None:
    for route in app.router.routes:
        methods = getattr(route, "methods", None)
        if methods:
            logger.info("ROUTE %s %s", ",".join(sorted(methods)), route.path)
        else:
            logger.info("ROUTE %s", route.path)


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/db/ping")
def db_ping():
    try:
        with engine.connect() as conn:
            conn.exec_driver_sql("SELECT 1")
        return {"status": "ok"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ping a la base de datos fallido: {type(e).__name__}: {e}")

# --- NOOP webhook para silenciar 404 de scrapers externos ---
@app.api_route("/webhook", methods=["GET", "POST"], include_in_schema=False)
def noop_webhook():
    return Response(status_code=200)
# --- fin NOOP webhook ---




ConversationState = Dict[str, Any]
SESSIONS: Dict[str, ConversationState] = {}


def norm(txt: str) -> str:
    txt = txt or ""
    txt = txt.lower().strip()
    txt = unicodedata.normalize("NFD", txt)
    return "".join(c for c in txt if unicodedata.category(c) != "Mn")


def reset_session(chat_id: str) -> ConversationState:
    state = {"stage": "ask_dni", "data": {}}
    SESSIONS[chat_id] = state
    return state


def send_message(chat_id: int, text: str):
    resp = requests.post(
        f"{TELEGRAM_API}/sendMessage",
        json={"chat_id": chat_id, "text": text},
        timeout=10,
    )
    if resp.status_code >= 400:
        logger.error("Telegram send error: %s %s", resp.status_code, resp.text)


def format_dt(dt: datetime) -> str:
    local_dt = dt.astimezone(TZ)
    return local_dt.strftime("%A %d/%m/%Y a las %H:%M")


def compute_slot(option: str) -> Optional[datetime]:
    now = datetime.now(TZ)
    if option == "1":
        candidate = now.replace(hour=16, minute=0, second=0, microsecond=0)
        if candidate <= now:
            candidate += timedelta(days=1)
        return candidate
    if option == "2":
        candidate = (now + timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)
        return candidate
    return None


LOCATIONS = {
    "1": "Hospital de Especialidades - Torre Sur, C.204 (Guayaquil)\nGPS: https://maps.app.goo.gl/7J8v9V9RJHfxADfz7",
    "2": "Clínica Santa Elena (Milagro)\nGPS: https://maps.app.goo.gl/dxZqqW91yS5JLF79A",
}


@app.post("/telegram/webhook")
async def telegram_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
):
    expected_secret = os.getenv("TELEGRAM_WEBHOOK_SECRET")
    if expected_secret:
        if x_telegram_bot_api_secret_token != expected_secret:
            logging.warning("Webhook rechazado: secret inválido o ausente")
            raise HTTPException(status_code=403, detail="Forbidden")
        logging.info("Webhook recibido con secret válido")
    else:
        logging.debug("Webhook recibido sin secret configurado")

    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    background_tasks.add_task(process_update, payload)
    return {"ok": True}


def process_update(payload: Dict[str, Any]) -> None:
    try:
        message = payload.get("message") or payload.get("edited_message")
        if not message:
            return

        chat = message.get("chat", {}).get("id")
        if not chat:
            return

        text = (message.get("text") or "").strip()
        if not text:
            send_message(chat, "Solo puedo leer texto por ahora, ¿puedes escribirlo?")
            return

        state = SESSIONS.get(str(chat)) or reset_session(str(chat))
        normalized = norm(text)
        data = state["data"]

        with Session(engine) as db:
            if state["stage"] == "ask_dni":
                if not re.fullmatch(r"[0-9]{9,12}", normalized):
                    send_message(chat, "Necesito tu número de cédula (9-12 dígitos). Inténtalo nuevamente.")
                    return
                data["dni"] = normalized
                patient = get_patient_by_dni(db, normalized)
                if patient:
                    data["patient"] = patient
                    state["stage"] = "confirm_existing"
                    send_message(
                        chat,
                        f"Tengo registrado a {patient.full_name} (Tel: {patient.phone or 'sin teléfono'}). "
                        "¿Deseas usar estos datos? (responde sí/no)",
                    )
                else:
                    state["stage"] = "ask_name"
                    send_message(chat, "Perfecto, ¿Cuál es tu nombre completo?")
                return

            if state["stage"] == "confirm_existing":
                if normalized in {"si", "sí", "claro", "ok"}:
                    patient = data["patient"]
                    data["patient_id"] = patient.id
                    data["full_name"] = patient.full_name
                    data["phone"] = patient.phone or ""
                    state["stage"] = "choose_location"
                    send_message(chat, "¿En qué sede prefieres atenderte? 1) Guayaquil  2) Milagro")
                elif normalized in {"no", "prefiero no"}:
                    state["stage"] = "ask_name"
                    send_message(chat, "Entendido. ¿Cuál es tu nombre completo?")
                else:
                    send_message(chat, "Responde con sí o no para continuar.")
                return

            if state["stage"] == "ask_name":
                data["full_name"] = text.strip()
                state["stage"] = "ask_phone"
                send_message(chat, "¿Cuál es tu número de teléfono o WhatsApp?")
                return

            if state["stage"] == "ask_phone":
                data["phone"] = text.strip()
                state["stage"] = "choose_location"
                send_message(chat, "¿En qué sede prefieres atenderte? 1) Guayaquil  2) Milagro")
                return

            if state["stage"] == "choose_location":
                if normalized not in LOCATIONS:
                    send_message(chat, "Elige 1 para Guayaquil o 2 para Milagro.")
                    return
                data["location"] = LOCATIONS[normalized]
                state["stage"] = "choose_slot"
                send_message(chat, "Disponibilidad: 1) Hoy tarde (16:00)  2) Mañana mañana (10:00). ¿Cuál prefieres?")
                return

            if state["stage"] == "choose_slot":
                slot = compute_slot(normalized)
                if not slot:
                    send_message(chat, "Elige 1 o 2 para definir el horario.")
                    return

                end_dt = slot + timedelta(minutes=APPT_DURATION_MIN)
                dni = data["dni"]

                patient = data.get("patient")
                if not patient:
                    patient = upsert_patient(
                        db,
                        dni=dni,
                        full_name=data.get("full_name", ""),
                        phone=data.get("phone", ""),
                    )
                else:
                    patient = upsert_patient(
                        db,
                        dni=dni,
                        full_name=data.get("full_name", patient.full_name),
                        phone=data.get("phone", patient.phone or ""),
                    )

                calendar_result = create_calendar_event(
                    summary="Consulta con el Dr. Guzmán",
                    description=f"Paciente: {patient.full_name}\nCédula: {patient.dni}\nCanal: Telegram",
                    start_dt=slot,
                    duration_minutes=APPT_DURATION_MIN,
                    location=data["location"],
                )

                event_id = None
                html_link = None
                if calendar_result:
                    event_id = calendar_result.get("id")
                    html_link = calendar_result.get("htmlLink")

                appointment = create_appointment(
                    db,
                    patient_id=patient.id,
                    start_at=slot,
                    end_at=end_dt,
                    location=data["location"],
                    source="telegram",
                    calendar_event_id=event_id,
                    calendar_link=html_link,
                )

                confirmation = (
                    f"¡Listo {patient.full_name}! Reservé tu cita para {format_dt(appointment.start_at)}.\n"
                    f"Sede:\n{data['location']}"
                )
                if html_link:
                    confirmation += f"\nLink del evento: {html_link}"

                send_message(chat, confirmation)
                reset_session(str(chat))
                return

        send_message(chat, "No entendí tu mensaje. Vamos a empezar de nuevo. Indica tu número de cédula, por favor.")
        reset_session(str(chat))
    except Exception:
        logging.exception("telegram update failed")

