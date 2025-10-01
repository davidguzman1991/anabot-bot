
"""
Entrypoint principal para AnaBot.
"""
from __future__ import annotations

# Standard library
import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict

# Third-party libraries
import httpx
import psycopg2
from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware

# Internal imports
import db_utils
from utils.idempotency import mark_processed, is_processed
from config import get_settings
from flow_engine import FlowEngine
from session_store import FlowSessionStore

logger = logging.getLogger("anabot")
logging.basicConfig(level=logging.DEBUG)
# Bloque para arranque directo con manejo de errores global
if __name__ == "__main__":
    import uvicorn
    try:
        uvicorn.run("main:app", host="0.0.0.0", port=8080, reload=True)
    except Exception:
        logger.exception("Error al iniciar AnaBot")

settings = get_settings()
DATABASE_URL = settings.DATABASE_URL

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or settings.TELEGRAM_TOKEN
if not TELEGRAM_BOT_TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN/TELEGRAM_TOKEN env var is required")

TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
TELEGRAM_SECRET = os.getenv("TELEGRAM_WEBHOOK_SECRET", "")

WA_TOKEN = os.getenv("WHATSAPP_TOKEN", "")
WA_PHONE_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")
WA_VERIFY = os.getenv("WHATSAPP_VERIFY_TOKEN", "")
WA_MSG_URL = "https://graph.facebook.com/v20.0/{phone_id}/messages"

FLOW_PATH = Path(__file__).with_name("flow.json")
SESSION_STORE = FlowSessionStore()
FLOW_ENGINE: FlowEngine | None = None
SCHEMA_READY = False
FOOTER_TEXT = "\n\n0 Hablar con humano - 1/9 Inicio / Atras"

app = FastAPI(title="AnaBot", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_headers=["*"],
    allow_methods=["*"],
)


def ensure_schema_once() -> None:
    global SCHEMA_READY
    if SCHEMA_READY:
        return
    if not DATABASE_URL:
        logger.warning("DATABASE_URL not set; skipping schema init")
        SCHEMA_READY = True
        return
    sql_path = Path(__file__).with_name("db_init.sql")
    if not sql_path.exists():
        SCHEMA_READY = True
        return
    statements = [segment.strip() for segment in sql_path.read_text(encoding="utf-8").split(";") if segment.strip()]
    if not statements:
        SCHEMA_READY = True
        return
    try:
        with psycopg2.connect(DATABASE_URL) as conn:
            with conn.cursor() as cur:
                for stmt in statements:
                    cur.execute(stmt)
            conn.commit()
        SCHEMA_READY = True
    except Exception:
        logger.exception("Failed to ensure database schema")
        raise


def get_flow_engine() -> FlowEngine:
    global FLOW_ENGINE
    if FLOW_ENGINE is None:
        ensure_schema_once()
        FLOW_ENGINE = FlowEngine(flow_path=str(FLOW_PATH), store=SESSION_STORE)
    return FLOW_ENGINE


def _append_footer(message: str) -> str:
    message = (message or "").strip()
    if not message:
        message = "Gracias por escribirnos."
    if FOOTER_TEXT.strip() in message:
        return message
    return f"{message}{FOOTER_TEXT}"


@app.on_event("startup")
async def log_routes() -> None:
    for route in app.router.routes:
        methods = getattr(route, "methods", None)
        if methods:
            logger.info("ROUTE %s %s", ",".join(sorted(methods)), route.path)
        else:
            logger.info("ROUTE %s", route.path)


@app.get("/health")
async def health() -> dict[str, bool]:
    return {"ok": True}


@app.api_route("/webhook", methods=["GET", "POST"], include_in_schema=False)
async def noop_webhook() -> Response:
    return Response(status_code=200)


async def handle_text(user_text: str, platform: str, user_id: str) -> str:
    engine = get_flow_engine()
    clean_text = (user_text or "").strip()
    channel = "wa" if platform.lower().startswith("wa") else "tg"
    session_id = f"{channel}:{user_id}"
    db_utils.save_message(user_id, clean_text, channel)
    preview = clean_text.replace("\n", " ")[:120]
    logger.info("handle_text channel=%s user=%s len=%s preview=%s", channel, user_id, len(clean_text), preview)

    if clean_text == "0":
        engine.hooks.handoff_to_human(platform=channel, user_id=str(user_id), message=user_text, ctx={})
        response_text = _append_footer("Te conecto con un asesor humano y compartire tu mensaje.")
        db_utils.save_response(user_id, response_text, channel)
        return response_text

    state = SESSION_STORE.get(session_id)
    ctx = state.setdefault("ctx", {})
    meta = ctx.setdefault("meta", {})
    meta["channel"] = channel
    meta["platform"] = platform.lower()
    meta["user_id"] = str(user_id)
    ctx["last_text"] = clean_text
    state["ctx"] = ctx
    SESSION_STORE.set(session_id, state)

    result = engine.process(session_id, clean_text)
    post_state = SESSION_STORE.snapshot(session_id)
    payload = post_state.get("payload", {})

    patient_id = None
    agenda = payload.get("agenda") or {}
    patient = agenda.get("patient") or {}
    if patient.get("dni"):
        patient_id = patient["dni"]
    elif agenda.get("dni"):
        patient_id = agenda["dni"]

    final_state = SESSION_STORE.get(session_id)
    final_state["ctx"] = payload
    final_state["patient_id"] = patient_id
    SESSION_STORE.set(session_id, final_state)

    message = (result or {}).get("message") or "Gracias por escribirnos."
    db_utils.save_response(user_id, message, channel)
    return _append_footer(message)

async def tg_send_text(chat_id: str, text: str) -> None:
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(
            f"{TELEGRAM_API}/sendMessage",
            json={"chat_id": chat_id, "text": text},
        )
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            logger.error(
                "Telegram send error: %s %s",
                exc.response.status_code if exc.response else "?",
                exc.response.text if exc.response else exc,
            )


async def wa_send_text(to_number: str, text: str) -> None:
    if not (WA_TOKEN and WA_PHONE_ID):
        logger.error("WhatsApp disabled: missing env vars.")
        return
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(
            WA_MSG_URL.format(phone_id=WA_PHONE_ID),
            headers={
                "Authorization": f"Bearer {WA_TOKEN}",
                "Content-Type": "application/json",
            },
            json={
                "messaging_product": "whatsapp",
                "to": to_number,
                "type": "text",
                "text": {"body": text},
            },
        )
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            logger.error(
                "WhatsApp send error: %s %s",
                exc.response.status_code if exc.response else "?",
                exc.response.text if exc.response else exc,
            )


@app.get("/webhook/whatsapp")
async def wa_verify(
    mode: str | None = Query(None, alias="hub.mode"),
    challenge: str | None = Query(None, alias="hub.challenge"),
    token: str | None = Query(None, alias="hub.verify_token"),
    mode2: str | None = Query(None, alias="mode"),
    challenge2: str | None = Query(None, alias="challenge"),
    token2: str | None = Query(None, alias="token"),
):
    m = (mode or mode2 or "").strip()
    t = (token or token2 or "").strip()
    c = (challenge or challenge2 or "")
    if m == "subscribe" and t == (WA_VERIFY or "").strip():
        return int(c) if c.isdigit() else (c or "")
    raise HTTPException(status_code=403, detail="Verification failed")



from hooks import get_daypart_greeting, is_greeting, format_main_menu, is_red_flag, reset_to_main, compose_greeting, inactivity_middleware, send_greeting_with_menu, build_info_servicios_message
from session_store import FlowSessionStore

@app.post("/webhook/whatsapp")
async def wa_webhook(request: Request) -> dict[str, bool]:
    body = await request.json()
    try:
        entry = (body.get("entry") or [{}])[0]
        changes = (entry.get("changes") or [{}])[0]
        value = changes.get("value") or {}
        messages = value.get("messages") or []
        statuses = value.get("statuses") or []

        for message in messages:
            from_number = message.get("from")
            msg_type = message.get("type")
            message_id = message.get("id") or message.get("message_id")
            if not from_number or not message_id:
                continue
            # Idempotencia: si ya procesado, no responder
            if is_processed(message_id, "wa"):
                continue
            user_text = ""
            if msg_type == "text":
                user_text = message["text"].get("body", "")
            elif msg_type == "reaction":
                user_text = f"Reaction {message['reaction'].get('emoji', '')}".strip()
            text = (user_text or "").strip().lower()
            preview = text.replace("\n", " ")[:120]
            logger.info("WA incoming user=%s len=%s preview=%s", from_number, len(text), preview)

            # Cargar o crear sesión
            session_store = SESSION_STORE
            session_id = f"wa:{from_number}"
            session = session_store.get(session_id)

            # Llamar al middleware de inactividad: puede enviar despedida y reiniciar
            try:
                handled = await inactivity_middleware(from_number, wa_send_text, text)
                if handled:
                    mark_processed(message_id, "wa")
                    continue  # ya se envió despedida + saludo+menú
            except Exception:
                logger.exception("Inactivity middleware failed")

            # Forzar MENÚ PRINCIPAL correctamente (sin duplicar “Soy Ana…”)
            if is_greeting(text) or session.get("state") not in {"MENU_PRINCIPAL", "RF_RED_FLAG"}:
                if not session.get("has_greeted"):
                    try:
                        await send_greeting_with_menu(from_number, wa_send_text)
                    except Exception:
                        logger.exception("WhatsApp delivery failed (greeting+menu)")
                    session["has_greeted"] = True
                else:
                    try:
                        await wa_send_text(from_number, format_main_menu())
                    except Exception:
                        logger.exception("WhatsApp delivery failed (menu)")
                reset_to_main(session)
                session_store.set(session_id, session)
                logger.info("INFO:anabot:FORCED_MENU user=%s text='%s' state='%s'", from_number, text, session.get("state"))
                mark_processed(message_id, "wa")
                continue

            # Red flag detection
            if is_red_flag(text):
                session["state"] = "RF_RED_FLAG"
                session_store.set(session_id, session)
                red_flag_msg = ("💛 Lamento lo que sientes. Puedo ayudarte con una cita prioritaria.\n"
                                "Si los síntomas son muy intensos, acude a Emergencias o llama al 911.\n"
                                "0️⃣ Atrás · 1️⃣ Agendar cita prioritaria · 2️⃣ Hablar con el Dr. Guzmán · 9️⃣ Inicio")
                logger.info(f"INFO:anabot:RED_FLAG_DETECTED for user {from_number} text='{text}'")
                mark_processed(message_id, "wa")
                db_utils.save_response(from_number, red_flag_msg, "wa")
                try:
                    await wa_send_text(from_number, red_flag_msg)
                except Exception:
                    logger.exception("WhatsApp response delivery failed")
                continue

            # Menú principal router
            if session.get("state") == "MENU_PRINCIPAL":
                if text in {"0", "9", "1", "2", "3", "4", "5"}:
                    logger.debug(f"MENU_PRINCIPAL: user={from_number}, text={text}, state={session.get('state')}")
                    if text == "0":
                        menu = format_main_menu()
                        reply = f"{menu}"
                        logger.info(f"INFO:anabot:MENU_OPTION 0 (atrás) for user {from_number}")
                        session["state"] = "MENU_PRINCIPAL"
                        session_store.set(session_id, session)
                    elif text == "9":
                        reset_to_main(session)
                        session_store.set(session_id, session)
                        menu = format_main_menu()
                        reply = f"{menu}"
                        logger.info(f"INFO:anabot:MENU_OPTION 9 (inicio) for user {from_number}")
                    elif text == "1":
                        # Más información de servicios
                        reply = build_info_servicios_message()
                        session["state"] = "INFO_SERVICIOS"
                        session_store.set(session_id, session)
                        logger.info(f"INFO:anabot:MENU_OPTION 1 (info servicios) for user {from_number}, state={session['state']}")
                    elif text == "2":
                        # Agendar cita médica
                        reply = "Por favor, ingrese su número de cédula (10 dígitos) o pasaporte:"
                        session["state"] = "AGENDAR_CITA_DNI"
                        session_store.set(session_id, session)
                        logger.info(f"INFO:anabot:MENU_OPTION 2 (agendar cita) for user {from_number}")
                    elif text in {"3", "4", "5"}:
                        reply = "⚙️ En construcción"
                        logger.info(f"INFO:anabot:MENU_OPTION {text} for user {from_number}")
                    else:
                        reply = format_main_menu()
                    logger.debug(f"RESPUESTA: user={from_number}, reply={reply}, state={session.get('state')}")
                    mark_processed(message_id, "wa")
                    db_utils.save_response(from_number, reply, "wa")
                    try:
                        await wa_send_text(from_number, reply)
                    except Exception:
                        logger.exception("WhatsApp response delivery failed")
                    continue

            # Flujo de agendamiento de cita médica
            if session.get("state") == "AGENDAR_CITA_DNI":
                dni = text.strip()
                # Validar cédula (10 dígitos) o pasaporte (alfanumérico)
                paciente = None
                if len(dni) == 10 and dni.isdigit():
                    # Simulación de búsqueda: si el dni termina en 1, existe
                    if dni.endswith("1"):
                        paciente = {"nombre": "Juan Pérez"}  # Simulación, reemplaza por consulta real
                # Si paciente existe, saltar a selección de día
                if paciente:
                    reply = (
                        f"Usted es el paciente {paciente['nombre']}. Indique qué día desea ser atendido, por favor marque el número de las siguientes opciones:\n"
                        "1. Hoy\n2. Mañana\n3. Otra fecha\n0. Atrás\n9. Inicio"
                    )
                    session["state"] = "AGENDAR_CITA_DIA"
                    session["dni"] = dni
                    session["nombre"] = paciente["nombre"]
                    session_store.set(session_id, session)
                    mark_processed(message_id, "wa")
                    db_utils.save_response(from_number, reply, "wa")
                    try:
                        await wa_send_text(from_number, reply)
                    except Exception:
                        logger.exception("WhatsApp response delivery failed")
                    continue
                else:
                    reply = "Escribir un nombre y dos apellidos (por ej: Maria Lopez Garcia)"
                    session["state"] = "AGENDAR_CITA_NOMBRE"
                    session["dni"] = dni
                    session_store.set(session_id, session)
                    mark_processed(message_id, "wa")
                    db_utils.save_response(from_number, reply, "wa")
                    try:
                        await wa_send_text(from_number, reply)
                    except Exception:
                        logger.exception("WhatsApp response delivery failed")
                    continue
            if session.get("state") == "AGENDAR_CITA_NOMBRE":
                nombre = text.strip()
                reply = "Ayúdeme digitando su Fecha de nacimiento (DD–MM–AAAA) por ej: 20–06–1991"
                session["state"] = "AGENDAR_CITA_FECHA"
                session["nombre"] = nombre
                session_store.set(session_id, session)
                mark_processed(message_id, "wa")
                db_utils.save_response(from_number, reply, "wa")
                try:
                    await wa_send_text(from_number, reply)
                except Exception:
                    logger.exception("WhatsApp response delivery failed")
                continue
            if session.get("state") == "AGENDAR_CITA_FECHA":
                fecha = text.strip()
                reply = "Ayúdeme proporcionando su número de contacto ya sea celular o whatsapp por ej: 09xxxxxxxx"
                session["state"] = "AGENDAR_CITA_TELEFONO"
                session["fecha_nacimiento"] = fecha
                session_store.set(session_id, session)
                mark_processed(message_id, "wa")
                db_utils.save_response(from_number, reply, "wa")
                try:
                    await wa_send_text(from_number, reply)
                except Exception:
                    logger.exception("WhatsApp response delivery failed")
                continue
            if session.get("state") == "AGENDAR_CITA_TELEFONO":
                telefono = text.strip()
                reply = "Ayúdeme con una dirección de correo electrónico por ej: xxxxxxx@mail.com. En el caso de no tenerlo por favor escribir ninguno para seguir avanzando"
                session["state"] = "AGENDAR_CITA_EMAIL"
                session["telefono"] = telefono
                session_store.set(session_id, session)
                mark_processed(message_id, "wa")
                db_utils.save_response(from_number, reply, "wa")
                try:
                    await wa_send_text(from_number, reply)
                except Exception:
                    logger.exception("WhatsApp response delivery failed")
                continue
            if session.get("state") == "AGENDAR_CITA_EMAIL":
                email = text.strip()
                reply = (
                    "Indique qué día desea ser atendido, por favor marque el número de las siguientes opciones:\n"
                    "1. Hoy\n2. Mañana\n3. Otra fecha\n0. Atrás\n9. Inicio"
                )
                session["state"] = "AGENDAR_CITA_DIA"
                session["email"] = email
                session_store.set(session_id, session)
                mark_processed(message_id, "wa")
                db_utils.save_response(from_number, reply, "wa")
                try:
                    await wa_send_text(from_number, reply)
                except Exception:
                    logger.exception("WhatsApp response delivery failed")
                continue

            # Si no entró por ninguna de las anteriores, NO llamar router legacy
            # (no llamar handle_text ni lógica de DNI)

        if statuses:
            logger.info("WA statuses: %s", json.dumps(statuses)[:200])

    except Exception:
        logger.exception("WhatsApp webhook processing failed")
    return {"ok": True}


@app.post("/telegram/webhook")
async def telegram_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
) -> dict[str, bool]:
    if TELEGRAM_SECRET and x_telegram_bot_api_secret_token != TELEGRAM_SECRET:
        logger.warning("Telegram webhook rejected: invalid secret")
        raise HTTPException(status_code=403, detail="Forbidden")

    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    background_tasks.add_task(asyncio.create_task, process_telegram_update(payload))
    return {"ok": True}


async def process_telegram_update(payload: Dict[str, Any]) -> None:
    try:
        message = payload.get("message") or payload.get("edited_message")
        if not message:
            return

        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        if chat_id is None:
            return
        chat_id = str(chat_id)

        user_text = (message.get("text") or "").strip()
        message_id = str(message.get("message_id") or message.get("message_id") or message.get("message_id"))
        preview = user_text.replace("\n", " ")[:120]
        logger.info("TG incoming user=%s len=%s preview=%s", chat_id, len(user_text), preview)

        # Idempotencia: si ya procesado, no responder
        if not message_id or is_processed(message_id, "tg"):
            return
        response = await handle_text(user_text, platform="telegram", user_id=chat_id)
        mark_processed(message_id, "tg")
        if response:
            await tg_send_text(chat_id, response)
    except Exception:
        logger.exception("Telegram webhook processing failed")











