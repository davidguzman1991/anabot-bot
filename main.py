# main.py — ANA (v5.1)
# -------------------------------------------------------------
# ✅ (1) Evita choques: verifica disponibilidad con Google Calendar (freeBusy)
#     y sugiere alternativas dentro del horario de atención.
# ✅ (2) Horario por sede (Guayaquil/Milagro) + feriados (configurables).
# ✅ (3) Reagendar / Cancelar: mover o anular la cita existente (Calendar).
# ✅ (4) Recordatorios: Telegram (predeterminado, gratis) y WhatsApp (si hay número).
# ✅ (5) FAQs estratégicas (presentación, medicina basada en evidencia, NO terapias
#        alternativas/naturales, y aclaración de atención privada / no IESS) + CTA.
#
# Requisitos básicos:
#   pip install fastapi uvicorn dateparser python-dotenv requests
#   pip install google-api-python-client google-auth-httplib2 google-auth-oauthlib
#   (opcional p/ recordatorios) pip install apscheduler
#
# Variables de entorno necesarias (Railway):
#   TELEGRAM_BOT_TOKEN=...
#   TELEGRAM_CHAT_ID=...
#   GOOGLE_CALENDAR_ID=primary
#   APPT_DURATION_MIN=45
#   ANA_VERIFY=ANA_CHATBOT
#   WHATSAPP_TOKEN=...
#   WHATSAPP_PHONE_ID=...
#   PORT=8080
#   # Para credenciales de Google (opción práctica en Railway):
#   GOOGLE_TOKEN_JSON={...contenido completo de token.json...}
#
# Ejecutar local:
#   .\.venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8000 --reload
# -------------------------------------------------------------

from __future__ import annotations
import os, re
from typing import Dict, List, Optional
from datetime import datetime, timedelta, time
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Request, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dateparser import parse as dp_parse
from dotenv import load_dotenv
import requests

# Intentar cargar APScheduler (opcional)
try:
    from apscheduler.schedulers.background import BackgroundScheduler
    SCHED_AVAILABLE = True
except Exception:
    BackgroundScheduler = None
    SCHED_AVAILABLE = False

# =========================
# BLOQUE 1: utilidades WA
# =========================
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN", "")
WHATSAPP_PHONE_ID = os.getenv("WHATSAPP_PHONE_ID", "")
ANA_VERIFY = os.getenv("ANA_VERIFY", "ANA_CHATBOT")
PORT = os.getenv("PORT", "8080")  # Railway suele usar 8080

def wa_send_text(to: str, body: str):
    """Envía un texto por WhatsApp Cloud."""
    url = f"https://graph.facebook.com/v19.0/{WHATSAPP_PHONE_ID}/messages"
    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json"
    }
    data = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": (body or "")[:4096]}
    }
    r = requests.post(url, headers=headers, json=data, timeout=20)
    # Si algo falla, lanza excepción para que lo veas en logs
    r.raise_for_status()
    return r.json()

def chat_reply_via_http(session_id: str, text: str) -> str:
    """Llama a /chat por HTTP dentro del mismo servicio y devuelve el 'reply'.
    Usa INTERNAL_CHAT_URL si está definida; si no, asume http://127.0.0.1:{PORT}/chat.
    Local: PORT defaults to 8000. En Railway: PORT lo inyecta la plataforma.
    """
    import os, requests
    chat_url = os.getenv("INTERNAL_CHAT_URL")
    if not chat_url:
        port = os.getenv("PORT", "8000")
        chat_url = f"http://127.0.0.1:{port}/chat"
    try:
        resp = requests.post(
            chat_url,
            json={"session_id": session_id, "text": text},
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("reply", "Gracias, lo reviso y le confirmo.")
    except Exception:
        return "Gracias, lo reviso y le confirmo."

# Google Calendar
SCOPES = ["https://www.googleapis.com/auth/calendar"]

# Cargar .env (si existe)
load_dotenv()

# --- Escribir token.json desde variable de entorno (útil en Railway) ---
GOOGLE_TOKEN_JSON = os.getenv("GOOGLE_TOKEN_JSON")
if GOOGLE_TOKEN_JSON:
    try:
        if not os.path.exists("token.json"):
            with open("token.json", "w", encoding="utf-8") as f:
                f.write(GOOGLE_TOKEN_JSON.strip())
    except Exception as e:
        print("WARN: no pude escribir token.json:", e)

# ------------ Config ------------
TZ = ZoneInfo("America/Guayaquil")
APPT_DURATION_MIN = int(os.getenv("APPT_DURATION_MIN", "45"))
GOOGLE_CALENDAR_ID = os.getenv("GOOGLE_CALENDAR_ID", "primary")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# Horarios por sede (0=Lunes ... 6=Domingo). Actualizado según memorias.
WORKING_HOURS = {
    "Guayaquil": {
        0: [("08:00","12:00"), ("16:00","19:30")],
        1: [("08:00","12:00"), ("16:00","19:30")],
        2: [("08:00","12:00"), ("16:00","19:30")],
        3: [("08:00","12:00"), ("16:00","19:30")],
        4: [("08:00","12:00"), ("16:00","19:30")],
        5: [("09:00","16:00")],                     # sábado
        6: []                                        # domingo solo emergencias, pero no agendable
    },
    "Milagro": {
        0: [("10:00","16:00")],  # lunes
        2: [("10:00","16:00")],  # miércoles
        4: [("10:00","16:00")],  # viernes
    }
}

HOLIDAYS = set([
    # "2025-12-25", "2026-01-01"
])

CLINIC_GYE = "Hospital de Especialidades de la ciudad, Torre Sur, consultorio 204 (antigua Clínica Kennedy Alborada). GPS: https://maps.app.goo.gl/7J8v9V9RJHfxADfz7"
CLINIC_MILAGRO = "Clínica Santa Elena (Av. Cristóbal Colón y Gral. P. J. Montero), Milagro. GPS: https://maps.app.goo.gl/sE2ehFSeDVWAQj867"
ATT_NOTE = "Atención previa cita."

# Branding / presentación breve
DOC_SUMMARY = ("El Dr. Guzmán es médico especialista en diabetes y sus complicaciones, "
               "con amplia experiencia y un enfoque en mejorar la calidad de vida de sus pacientes.")

# ------------ Modelos ------------
class ChatIn(BaseModel):
    session_id: str
    text: str

class ChatOut(BaseModel):
    reply: str

class Appointment(BaseModel):
    session_id: str
    when_iso: str
    where: str = "Guayaquil"
    event_id: Optional[str] = None
    created_at: str
    contact_name: Optional[str] = None
    contact_phone: Optional[str] = None
    status: str = "scheduled"
    reminder_ids: Optional[List[str]] = None

# ------------ App ------------
app = FastAPI(title="ANA — Asistente Médico", version="5.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

SESSIONS: Dict[str, Dict] = {}
APPOINTMENTS: List[Appointment] = []

# Scheduler global (si está disponible)
SCHED = BackgroundScheduler(timezone=str(TZ)) if SCHED_AVAILABLE else None
if SCHED:
    try:
        SCHED.start()
    except Exception:
        SCHED = None

# ------------ Utilidades de fecha/hora ------------
def parse_dt_es(text: str, ref: Optional[datetime] = None) -> Optional[datetime]:
    settings = {
        "PREFER_DATES_FROM": "future",
        "TIMEZONE": "America/Guayaquil",
        "RETURN_AS_TIMEZONE_AWARE": True,
    }
    if ref is not None:
        settings["RELATIVE_BASE"] = ref
    dt = dp_parse(text, languages=["es"], settings=settings)
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TZ)
    return dt.astimezone(TZ)

WEEKDAYS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]

def format_dt_es(dt: datetime) -> str:
    d = dt.astimezone(TZ)
    wd = WEEKDAYS[d.weekday()]
    return f"{wd} {d.day:02d}/{d.month:02d}/{d.year} a las {d.hour:02d}:{d.minute:02d}"

def hhmm_to_time(s: str) -> time:
    h, m = map(int, s.split(":"))
    return time(hour=h, minute=m, tzinfo=TZ)

def within_working_hours(dt: datetime, where: str) -> bool:
    day = dt.weekday()
    date_str = dt.strftime("%Y-%m-%d")
    windows = WORKING_HOURS.get(where, {}).get(day, [])
    if not windows or date_str in HOLIDAYS:
        return False
    for start, end in windows:
        ts, te = hhmm_to_time(start), hhmm_to_time(end)
        if ts <= dt.timetz().replace(tzinfo=TZ) < te:
            end_dt = dt + timedelta(minutes=APPT_DURATION_MIN)
            if ts <= end_dt.timetz().replace(tzinfo=TZ) <= te:
                return True
    return False

def next_open_slot(dt: datetime, where: str, step_min: int = 15, max_days_ahead: int = 30) -> Optional[datetime]:
    """Encuentra el próximo inicio disponible dentro de horarios (sin consultar Calendar)."""
    curr = dt.astimezone(TZ).replace(second=0, microsecond=0)
    for _ in range(int((max_days_ahead*24*60)/step_min)):
        if within_working_hours(curr, where):
            return curr
        curr += timedelta(minutes=step_min)
    return None

# ------------ Integraciones externas ------------
def notify_telegram(text: str) -> bool:
    """Envía un mensaje por Telegram al TELEGRAM_CHAT_ID configurado."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return False
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
        r = requests.post(url, json=payload, timeout=10)
        return r.ok
    except Exception:
        return False

def notify_whatsapp(phone: str, message: str) -> bool:
    """Envía mensaje de WhatsApp usando Cloud API (si hay credenciales). phone con código país: 5939XXXXXXX"""
    if not WHATSAPP_TOKEN or not WHATSAPP_PHONE_ID or not phone:
        return False
    try:
        return bool(wa_send_text(phone, message))
    except Exception:
        return False

def get_calendar_service():
    # 1) Tu módulo propio (si lo tienes)
    try:
        from auth_google import get_calendar_service as _get
        return _get()
    except Exception:
        pass
    # 2) token.json local
    try:
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
        if not os.path.exists("token.json"):
            return None
        creds = Credentials.from_authorized_user_file("token.json", SCOPES)
        return build("calendar", "v3", credentials=creds)
    except Exception:
        return None

def is_slot_free(start_dt: datetime, duration_min: int, calendar_id: str = None) -> Optional[bool]:
    """Consulta FreeBusy. Devuelve True/False si hay servicio; None si no se puede verificar."""
    svc = get_calendar_service()
    if svc is None:
        return None
    if calendar_id is None:
        calendar_id = GOOGLE_CALENDAR_ID
    end_dt = start_dt + timedelta(minutes=duration_min)
    body = {"timeMin": start_dt.isoformat(), "timeMax": end_dt.isoformat(), "items": [{"id": calendar_id}]}
    try:
        res = svc.freebusy().query(body=body).execute()
        busy = res["calendars"][calendar_id]["busy"]
        return len(busy) == 0
    except Exception:
        return None

def suggest_alternatives(start_dt: datetime, where: str, n: int = 3) -> List[datetime]:
    """Propone hasta n alternativas libres cercanas (requiere horario; usa freeBusy si hay)."""
    suggestions = []
    candidate = next_open_slot(start_dt, where) or start_dt
    visited = 0
    while len(suggestions) < n and visited < 200:
        if within_working_hours(candidate, where):
            ok = is_slot_free(candidate, APPT_DURATION_MIN)
            if ok is None or ok:
                suggestions.append(candidate)
        candidate += timedelta(minutes=15)
        visited += 1
    return suggestions

def create_calendar_event(start_dt: datetime, where: str, title: str, description: str) -> Optional[str]:
    svc = get_calendar_service()
    if svc is None:
        return None
    end_dt = start_dt + timedelta(minutes=APPT_DURATION_MIN)
    location = CLINIC_GYE if where.lower().startswith("g") else CLINIC_MILAGRO
    body = {
        "summary": title,
        "location": location,
        "description": description,
        "start": {"dateTime": start_dt.isoformat(), "timeZone": "America/Guayaquil"},
        "end":   {"dateTime": end_dt.isoformat(),   "timeZone": "America/Guayaquil"},
    }
    try:
        ev = svc.events().insert(calendarId=GOOGLE_CALENDAR_ID, body=body, sendUpdates="all").execute()
        return ev.get("id")
    except Exception:
        return None

def move_calendar_event(event_id: str, new_start: datetime) -> bool:
    svc = get_calendar_service()
    if svc is None or not event_id:
        return False
    try:
        ev = svc.events().get(calendarId=GOOGLE_CALENDAR_ID, eventId=event_id).execute()
        new_end = new_start + timedelta(minutes=APPT_DURATION_MIN)
        ev["start"] = {"dateTime": new_start.isoformat(), "timeZone": "America/Guayaquil"}
        ev["end"]   = {"dateTime": new_end.isoformat(),   "timeZone": "America/Guayaquil"}
        svc.events().update(calendarId=GOOGLE_CALENDAR_ID, eventId=event_id, body=ev, sendUpdates="all").execute()
        return True
    except Exception:
        return False

def delete_calendar_event(event_id: str) -> bool:
    svc = get_calendar_service()
    if svc is None or not event_id:
        return False
    try:
        svc.events().delete(calendarId=GOOGLE_CALENDAR_ID, eventId=event_id, sendUpdates="all").execute()
        return True
    except Exception:
        return False

# ------------ Captura de contacto ------------
NAME_PAT = re.compile(r"(?:me llamo|mi nombre es|soy)\s+([A-ZÁÉÍÓÚÑ][a-záéíóúñ]+(?:\s+[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+){0,2})", re.IGNORECASE)
PHONE_PAT = re.compile(r"(\+?\d[\d\-\s]{7,}\d)")

def extract_name(text: str) -> Optional[str]:
    m = NAME_PAT.search(text)
    if m:
        return m.group(1).strip().title()
    bare = text.strip()
    if len(bare.split()) in (1,2) and any(c.isalpha() for c in bare):
        if bare.lower() not in {"hola","buenas","gracias","cita","agendar","reservar"}:
            return bare.title()
    return None

def normalize_phone(raw: str) -> str:
    return re.sub(r"\D", "", raw)

def valid_phone_ec(num: str) -> bool:
    if num.startswith("0") and len(num)==10 and num[1]=="9":
        return True
    if num.startswith("593") and len(num) in (11,12) and num[3]=="9":
        return True
    if num.startswith("09") and len(num)==10:
        return True
    return len(num) >= 9

def extract_phone(text: str) -> Optional[str]:
    m = PHONE_PAT.search(text)
    if m:
        return normalize_phone(m.group(1))
    return None

def extract_channel(text: str) -> Optional[str]:
    t = text.lower()
    if "whatsapp" in t or "wasap" in t or "wpp" in t:
        return "WhatsApp"
    if "llamada" in t or "llamar" in t or "teléfono" in t or "telefono" in t:
        return "Llamada"
    if "correo" in t or "email" in t or "mail" in t:
        return "Correo"
    return None

# ------------ Triage de emergencia ------------
RED_FLAGS = [
    "dolor en el pecho", "dolor torácico", "dificultad para respirar", "falta de aire",
    "convulsión", "convulsiones", "pérdida de conciencia", "perdida de conciencia",
    "desmayo", "hemorragia", "sangrado abundante", "fiebre alta", "sepsis",
    "herida abierta profunda", "debilidad súbita", "cara caída", "habla arrastrada",
    "signos de acv", "acv", "ictus",
    # Ampliado para síntomas de diabetes
    "sed excesiva", "hambre constante", "fatiga extrema", "visión borrosa", "heridas lentas", "infecciones frecuentes",
    "hormigueo", "quemazón", "calambres", "frialdad en pies", "neuropatía", "poliuria", "polidipsia", "polifagia",
    "calentura", "fiebre", "dolor en pies", "úlceras", "dolor crónico"
]

def red_flag_guard(text: str) -> Optional[str]:
    t = text.lower()
    if any(flag in t for flag in RED_FLAGS):
        return ("⚠️ Lamento mucho que estés pasando por esto. Has llegado al lugar correcto. "
                "El Dr. Guzmán, especialista en diabetes, puede ayudarte no solo con el control de la enfermedad y prevención de complicaciones, "
                "sino también en mejorar tu calidad de vida. ¿Qué te gustaría hacer?\n"
                "1. Más información sobre nuestros servicios\n"
                "2. Agendar una consulta\n"
                "3. Conversar directamente con el Dr. Guzmán")
    return None

# ------------ FAQs estratégicas ------------
def faq_flow(user_text: str) -> Optional[str]:
    t = user_text.lower()
    # Presentación/qué medicina usan
    if "medicina" in t and ("natural" not in t and "alternativ" not in t):
        return (f"Soy Ana, asistente virtual del Dr. Guzmán. {DOC_SUMMARY} "
                "En nuestra Unidad utilizamos <b>medicina tradicional basada en evidencia científica</b>, "
                "siguiendo protocolos médicos actualizados. "
                "¿Desea que le ayude a agendar una cita?")
    # Medicina natural / terapias alternativas
    if "natural" in t or "alternativ" in t:
        return ("Soy Ana, asistente del Dr. Guzmán. "
                "Nuestros tratamientos <b>no se basan</b> en medicina natural ni terapias alternativas. "
                "Trabajamos exclusivamente con <b>medicina tradicional respaldada por evidencia científica</b>. "
                "Si lo desea, puedo ayudarle a coordinar una cita.")
    # IESS / seguro social
    if "iess" in t or "seguro" in t or "seguro social" in t:
        return (f"Soy Ana, asistente del Dr. Guzmán. Nuestros servicios médicos son <b>netamente privados</b>. "
                f"{DOC_SUMMARY} ¿Quiere que le ayude a reservar su consulta?")
    # Quién es el Dr. Guzmán
    if "quien es el dr" in t or "quién es el dr" in t or "dr guzman" in t or "dr. guzman" in t:
        return (f"Soy Ana, asistente del Dr. Guzmán. {DOC_SUMMARY} "
                "Atendemos en Guayaquil y Milagro. ¿Le ayudo a agendar?")
    # Precio/costo
    if "precio" in t or "costo" in t or "cuanto cuesta" in t or "cuanto vale" in t:
        return ("La consulta cuesta $45 y dura aproximadamente 60 minutos para despejar dudas, conocer al paciente y ayudarle objetivamente. "
                "Incluye: valoración nutricional con plan personalizado, educación diabetológica, examen de neuropatía, riesgo cardiovascular/renal, "
                "electrocardiograma si necesario, y integración al programa de soporte del Dr. Guzmán. "
                "¿Deseas agendar? (sí/no)")
    # Dirección/ubicación
    if "direccion" in t or "ubicacion" in t or "ubicado" in t or "queda" in t:
        return ("En Guayaquil: Hospital de Especialidades de la Ciudad (antigua Clínica Kennedy Alborada), Torre Sur, Consultorio 204. GPS: https://maps.app.goo.gl/7J8v9V9RJHfxADfz7\n"
                "En Milagro: Clínica Santa Elena (Av. Cristóbal Colón y Gral. P. J. Montero). GPS: https://maps.app.goo.gl/sE2ehFSeDVWAQj867\n"
                "¿En cuál sede deseas agendar?")
    # Horarios
    if "horario" in t or "hora" in t or "cuando atienden" in t:
        return ("Previa cita: Lunes a Viernes 8:00-12:00 y 16:00-19:30. Sábado 9:00-16:00. Domingo solo emergencias. "
                "¿Qué día te conviene?")
    # Servicios
    if "servicios" in t or "que ofrecen" in t or "consiste la consulta" in t:
        return ("Servicios: Tratamiento de Diabetes (Tipo 1/2/Gestacional), Prediabetes, Hígado Graso, Sobrepeso/Obesidad, Pie Diabético, "
                "Curación de Heridas, Dolor Crónico, Neuropatía, Enfermedad Renal, Tiroides, Emergencias Diabéticas, Hospitalización/Domicilio, Insulinización. "
                "¿Más detalles o agendar?")
    # Urgencias/emergencias
    if "urgencia" in t or "emergencia" in t:
        return ("Esta línea es para agendamientos y consultas. Para emergencias, puedo ayudarte a agendar rápido o comunícate directamente con el Dr. Guzmán al 0962062122 explicando tu caso.")
    return None

# ------------ Intenciones ------------
PAIN_KEYWORDS = [
    "dolor", "duele", "adolorido", "adolorida", "ardor",
    "hormigueo", "punzante", "quemazón", "quemazon", "calambre",
    "parestesias"
]
SCHEDULE_KEYWORDS = ["cita", "agendar", "agenda", "reservar", "reserva", "turno", "agéndame", "agendame"]
REBOOK_KEYWORDS = ["cambiar", "reagendar", "mover", "posponer", "modificar"]
CANCEL_KEYWORDS = ["cancelar", "anular", "eliminar la cita"]
YES_WORDS = {"si", "sí", "claro", "ok", "de acuerdo", "confirmo", "correcto", "está bien", "esta bien"}
NO_WORDS  = {"no", "cambiar", "otra hora", "otro dia", "otro día", "reagendar"}
WELCOME = "Hola, soy Ana — asistente del Dr. Guzmán. ¿En qué puedo ayudarle hoy?"

# ------------ Flujos ------------
def get_time_greeting():
    hour = datetime.now(TZ).hour
    if 5 <= hour < 12:
        return "¡Buenos días!"
    elif 12 <= hour < 18:
        return "¡Buenas tardes!"
    else:
        return "¡Buenas noches!"

def pain_flow(user_text: str, state: Dict) -> Optional[str]:
    t = user_text.lower()
    if any(k in t for k in PAIN_KEYWORDS):
        if not state.get("asked_pain_scale"):
            state["asked_pain_scale"] = True
            return ("Lamento que esté con dolor. Para entender mejor: "
                    "¿del 1 al 10 cuánto le duele y en qué parte?")
        if not state.get("asked_since_when"):
            state["asked_since_when"] = True
            return ("Gracias. ¿Desde cuándo lo siente y qué lo empeora o alivia? "
                    "¿Ha tomado algo que le ayude?")
        return ("Le escucho. Con esa información puedo orientar mejor los siguientes pasos. "
                "Si desea, puedo ayudarle a agendar una valoración.")
    return None

def schedule_contact_wizard(user_text: str, state: Dict, session_id: str) -> Optional[str]:
    contact = state.setdefault("contact", {})
    awaiting = state.get("awaiting")

    # Preguntar por sede si no está definida
    if not state.get("pending_where") and awaiting != "where":
        state["awaiting"] = "where"
        return "Por favor, ¿en qué sede desea atenderse? (Guayaquil o Milagro)"

    if awaiting == "where":
        t = user_text.lower()
        where = "Guayaquil" if "guayaquil" in t else "Milagro" if "milagro" in t else None
        if where:
            state["pending_where"] = where
            state["awaiting"] = "name"
            return f"Entendido, en {where}. Ahora, ¿me indica su nombre y apellido por favor?"
        return "Por favor, especifique Guayaquil o Milagro."

    if awaiting == "name":
        name = extract_name(user_text) or user_text.strip().title()
        contact["name"] = name
        parts = name.split()
        apellido = parts[-1] if len(parts) >= 2 else name
        state["awaiting"] = "cedula"
        return (f"Gracias. ¿Me indica su número de cédula por favor?")

    if awaiting == "cedula":
        contact["cedula"] = user_text.strip()
        state["awaiting"] = "birthdate"
        return "Gracias. ¿Me indica su fecha de nacimiento por favor? (ej.: 15/05/1980)"

    if awaiting == "birthdate":
        birth = dp_parse(user_text, languages=["es"])
        if birth:
            contact["birthdate"] = birth.isoformat()
            state["awaiting"] = "email"
            return "Perfecto, gracias. ¿Dirección de correo electrónico? (si no tiene, dígame 'no')"
        return "Por favor, indíqueme en formato día/mes/año."

    if awaiting == "email":
        contact["email"] = user_text.strip() if "no" not in user_text.lower() else None
        state["awaiting"] = "honorific"
        parts = contact["name"].split()
        apellido = parts[-1] if len(parts) >= 2 else contact["name"]
        return (f"Gracias. ¿Prefiere que me dirija como <b>Señor</b> o <b>Señora</b> {apellido}? "
                "(responda: señor / señora / señorita)")

    if awaiting == "honorific":
        t = user_text.lower()
        if "señorita" in t:
            contact["honorific"] = "Señorita"
        elif "señora" in t or "sra" in t:
            contact["honorific"] = "Señora"
        else:
            contact["honorific"] = "Señor"
        state["awaiting"] = "phone"
        return "¿Me confirma un número de teléfono o WhatsApp para contactarle por favor?"

    if awaiting == "phone":
        phone = extract_phone(user_text) or normalize_phone(user_text)
        if not phone or not valid_phone_ec(phone):
            return "Creo que ese número no es válido. ¿Podría escribirlo nuevamente por favor? (Ej.: 09XXXXXXXX)"
        contact["phone"] = phone if phone.startswith("593") else ("593" + phone.lstrip("0"))
        state["awaiting"] = "consent"
        return ("Para continuar, ¿autoriza el uso de sus datos con fines de agenda y comunicación médica? "
                "(responda: sí / no)")

    if awaiting == "consent":
        t = user_text.lower().strip()
        if t in {"si","sí","de acuerdo","ok"}:
            contact["consent"] = True
        else:
            contact["consent"] = False
            return ("Entiendo. Sin autorización no puedo finalizar la agenda. "
                    "Si cambia de opinión, indíqueme con 'sí'.")
        state["awaiting"] = "channel"
        return ("¿Por qué canal prefiere que le contactemos? (WhatsApp / llamada / correo)")

    if awaiting == "channel":
        channel = extract_channel(user_text) or user_text.strip().title()
        contact["preferred_channel"] = channel
        state.pop("awaiting", None)
        state["contact_ready"] = True
        return "Perfecto, gracias. Ya tengo sus datos. ¿Desea que confirme la cita ahora? (sí/no)"

    if not contact.get("name"):
        state["awaiting"] = "name"
        return "Para asistirle, ¿me indica su nombre y apellido por favor? (Ej.: 'Me llamo Juan Pérez')"
    state["contact_ready"] = True
    return None

def schedule_reminders(appt: Appointment):
    """Programa recordatorios por Telegram (siempre) y WhatsApp (si hay teléfono)."""
    if not SCHED:
        return
    when = datetime.fromisoformat(appt.when_iso)
    jobs = []
    for hours_before in (24, 2):
        run_at = when - timedelta(hours=hours_before)
        if run_at > datetime.now(TZ):
            msg = f"⏰ Recordatorio: cita {format_dt_es(when)} — {appt.where}"
            # Telegram siempre (si está configurado)
            try:
                job = SCHED.add_job(lambda m=msg: notify_telegram(m), 'date', run_date=run_at)
                jobs.append(job.id)
            except Exception:
                pass
            # WhatsApp si hay número y credenciales
            if appt.contact_phone:
                try:
                    job_w = SCHED.add_job(lambda m=msg, p=appt.contact_phone: notify_whatsapp(p, m),
                                          'date', run_date=run_at)
                    jobs.append(job_w.id)
                except Exception:
                    pass
    appt.reminder_ids = jobs or None

def schedule_inactivity_reminder(session_id: str):
    if not SCHED:
        return
    session = SESSIONS.get(session_id)
    if not session:
        return
    phone = session["state"].get("contact", {}).get("phone")
    first_reminder_time = datetime.now(TZ) + timedelta(minutes=20)
    SCHED.add_job(
        lambda: send_inactivity_message(session_id, "first", phone),
        'date', run_date=first_reminder_time
    )

def send_inactivity_message(session_id: str, level: str, phone: str):
    session = SESSIONS.get(session_id)
    if not session or "closed" in session["state"]:
        return
    msg = "¿Puedo ayudarte con algo más?" if level == "first" else "Doy por terminado el chat por ahora. ¡Espero poder resolver tus dudas pronto!"
    if phone:
        notify_whatsapp(phone, msg)
    else:
        notify_telegram(msg)
    if level == "first":
        second_time = datetime.now(TZ) + timedelta(minutes=20)
        SCHED.add_job(lambda: send_inactivity_message(session_id, "second", phone), 'date', run_date=second_time)
    elif level == "second":
        session["state"]["closed"] = True

def schedule_flow(user_text: str, state: Dict, session_id: str) -> Optional[str]:
    t = user_text.lower()

    # Recolección de datos en curso
    if state.get("awaiting") in {"where", "name","cedula","birthdate","email","honorific","phone","consent","channel"}:
        return schedule_contact_wizard(user_text, state, session_id)

    # Cancelación
    if any(k in t for k in CANCEL_KEYWORDS):
        last = next((a for a in reversed(APPOINTMENTS) if a.session_id == session_id and a.status=="scheduled"), None)
        if not last:
            return "No encuentro una cita activa para cancelar. ¿Podría indicarme la fecha aproximada por favor?"
        state["pending_cancel_event"] = last.event_id
        state["pending_cancel_idx"] = APPOINTMENTS.index(last)
        return (f"¿Desea cancelar su cita del {format_dt_es(datetime.fromisoformat(last.when_iso))} "
                f"en {last.where}? (sí/no)")

    if "pending_cancel_event" in state:
        if any(w in t for w in YES_WORDS):
            idx = state.pop("pending_cancel_idx", None)
            ev = state.pop("pending_cancel_event", None)
            ok = delete_calendar_event(ev)
            if idx is not None:
                APPOINTMENTS[idx].status = "canceled"
            notify_telegram("❌ Cita cancelada por el paciente.")
            return "Su cita ha sido cancelada. ¿Desea agendar una nueva fecha?"
        if any(w in t for w in NO_WORDS):
            state.pop("pending_cancel_event", None)
            state.pop("pending_cancel_idx", None)
            return "De acuerdo, mantenemos su cita. ¿En qué más puedo ayudarle?"
        return "¿Confirma la cancelación? (sí/no)"

    # Reagendar
    if any(k in t for k in REBOOK_KEYWORDS):
        new_dt = parse_dt_es(user_text)
        if not new_dt:
            state["rebook_intent"] = True
            return "Entiendo, ¿a qué día y hora desea mover su cita? (ej.: viernes 16:30)"
        state["pending_rebook_when"] = new_dt
        return f"¿Desea mover su cita a {format_dt_es(new_dt)}? (sí/no)"

    if state.get("rebook_intent"):
        new_dt = parse_dt_es(user_text)
        if new_dt:
            state["pending_rebook_when"] = new_dt
            state.pop("rebook_intent", None)
            return f"¿Desea mover su cita a {format_dt_es(new_dt)}? (sí/no)"
        return "¿Podría indíqueme la nueva fecha y hora por favor? (ej.: martes 10:00)"

    if "pending_rebook_when" in state:
        if any(w in t for w in YES_WORDS):
            last = next((a for a in reversed(APPOINTMENTS) if a.session_id == session_id and a.status=="scheduled"), None)
            if not last:
                state.pop("pending_rebook_when", None)
                return "No encuentro una cita activa para mover. ¿Desea crear una nueva?"
            new_start = state.pop("pending_rebook_when")
            where = last.where
            if not within_working_hours(new_start, where):
                nxt = next_open_slot(new_start, where)
                if nxt:
                    return (f"Ese horario está fuera de atención. ¿Le sirve {format_dt_es(nxt)}? (sí/no)")
                return "No encontré horario disponible cercano. Indíqueme otro horario por favor."
            ok = is_slot_free(new_start, APPT_DURATION_MIN)
            if ok is False:
                alts = suggest_alternatives(new_start, where)
                if alts:
                    s = "; ".join(format_dt_es(a) for a in alts)
                    return f"Esa hora está ocupada. Le propongo: {s}. ¿Cuál prefiere?"
                return "Esa hora está ocupada. Indíqueme otro horario, por favor."
            moved = move_calendar_event(last.event_id, new_start)
            if moved:
                last.when_iso = new_start.isoformat()
                notify_telegram(f"🔁 Cita reagendada a {format_dt_es(new_start)} — {where}")
                return f"Listo. Reagendé su cita a {format_dt_es(new_start)} en {where}."
            else:
                return "No pude mover la cita en el calendario. Intentemos con otro horario o cree una nueva cita."
        if any(w in t for w in NO_WORDS):
            state.pop("pending_rebook_when", None)
            return "De acuerdo, mantenemos su cita actual. ¿Desea otra cosa?"
        return "¿Confirma el cambio de horario? (sí/no)"

    # Nueva cita: confirmar propuesta existente
    if "pending_when" in state:
        if any(w in t for w in YES_WORDS):
            msg = schedule_contact_wizard(user_text="", state=state, session_id=session_id)
            if not state.get("contact_ready"):
                state["pending_confirmed"] = True
                return msg

            when: datetime = state.pop("pending_when")
            where = state.pop("pending_where", "Guayaquil")

            if not within_working_hours(when, where):
                nxt = next_open_slot(when, where)
                if nxt:
                    state["pending_when"] = nxt
                    state["pending_where"] = where
                    return f"Ese horario está fuera de atención. ¿Le sirve {format_dt_es(nxt)}? (sí/no)"
                return "No encontré horario disponible cercano. Indíqueme otro horario por favor."

            ok = is_slot_free(when, APPT_DURATION_MIN)
            if ok is False:
                alts = suggest_alternatives(when, where)
                s = "; ".join(format_dt_es(a) for a in alts) if alts else "otro horario"
                return f"Esa hora está ocupada. ¿Le sirve {s}?"

            contact = state.get("contact", {})
            honor = contact.get("honorific", "Señor/a")
            name = contact.get("name", "")
            desc = (f"Cita programada por ANA. Sesión: {session_id}. {ATT_NOTE}\n"
                    f"Paciente: {honor} {name}\n"
                    f"Teléfono: {contact.get('phone','(no informado)')}\n"
                    f"Canal: {contact.get('preferred_channel','(no informado)')}\n"
                    f"Cédula: {contact.get('cedula','(no informado)')}\n"
                    f"Fecha de nacimiento: {contact.get('birthdate','(no informado)')}\n"
                    f"Email: {contact.get('email','(no informado)')}")

            ev_id = create_calendar_event(when, where, "Consulta Aliviar", desc)

            appt = Appointment(
                session_id=session_id,
                when_iso=when.isoformat(),
                where=where,
                event_id=ev_id,
                created_at=datetime.now(TZ).isoformat(),
                contact_name=name or None,
                contact_phone=contact.get("phone"),
            )
            APPOINTMENTS.append(appt)

            # Recordatorios (Telegram + WhatsApp si hay teléfono)
            schedule_reminders(appt)

            lugar = CLINIC_GYE if where.lower().startswith("g") else CLINIC_MILAGRO
            cal_msg = "🗓️ Agregada al Google Calendar." if ev_id else "⚠️ No se pudo agregar al Calendar."
            notify_telegram(f"📅 Nueva cita: {format_dt_es(when)} — {where}\n👤 {honor} {name}\nID: {ev_id or 'sin ID'}")
            return (f"✅ ¡Listo! Gracias por agendar para {format_dt_es(when)} en {where}.\n"
                    f"📍 {lugar}\n{cal_msg}\nℹ️ {ATT_NOTE}\n"
                    f"Recuerda: Por seguridad, lleva un documento de ID. Si necesitas silla de ruedas, solicítala en entrada. "
                    f"Avisa con anticipación si retrasas o cancelas. Si deseas una pregunta puntual al Dr. Guzmán, llama al 0962062122.")

        if any(w in t for w in NO_WORDS):
            state.pop("pending_when")
            state.pop("pending_where", None)
            return "De acuerdo. Indíqueme otro día y hora que le vengan bien por favor."

        new_dt = parse_dt_es(user_text)
        if new_dt:
            state["pending_when"] = new_dt
            return f"¿Confirmo {format_dt_es(new_dt)}? (sí/no)"
        return "¿Confirma la fecha/hora propuesta? (sí/no) o indíqueme otra fecha."

    # Intención nueva de agendar
    if any(k in t for k in SCHEDULE_KEYWORDS):
        where = "Guayaquil"
        if "milagro" in t:
            where = "Milagro"
        state["pending_where"] = where

        dt = parse_dt_es(user_text)
        if dt is None:
            return ("Con gusto le ayudo a agendar. ¿Qué día y hora le vienen bien? "
                    "Ej.: 'jueves a las 10', '26/09 15:00', 'mañana 9 am', y la ciudad (Guayaquil/Milagro).")
        state["pending_when"] = dt
        return f"¿Le reservo {format_dt_es(dt)} en {where}? (sí/no)"

    # Memorizar intención para frases con fecha suelta
    if any(k in t for k in ("agenda", "agendar", "cita", "reservar")):
        state["agenda_context"] = True
    if state.get("agenda_context"):
        dt = parse_dt_es(user_text)
        if dt:
            state["pending_when"] = dt
            return f"¿Confirmo {format_dt_es(dt)}? (sí/no)"

    return None

def generic_reply(user_text: str, state: Dict) -> str:
    t = user_text.lower()
    if not state.get("welcomed"):
        state["welcomed"] = True
        greeting = get_time_greeting()
        return (f"{greeting} Soy Ana, la asistente virtual del Dr. Guzmán, encargada de gestionar citas y brindarte toda la información necesaria. "
                f"El Dr. Guzmán es especialista en diabetes y sus complicaciones, con un enfoque en mejorar la calidad de vida de los pacientes. "
                f"¿En qué puedo ayudarte? Elige una opción:\n"
                f"1. Precio de la consulta\n"
                f"2. Direcciones y ubicaciones\n"
                f"3. En qué consiste la consulta médica\n"
                f"4. Contactar directamente con el Dr. Guzmán para un tema específico\n"
                f"5. Agendar una cita\n"
                f"O dime si necesitas algo más.")
    if "deseo mas informacion" in t:
        return (f"Hola! Soy Ana la asistente del Dr. Guzmán, en qué puedo ayudarte específicamente. "
                f"Cabe aclarar que el Dr. Guzmán es un profesional en el área de la Diabetes y estará gustoso de poder ayudarte. "
                f"Elige una opción:\n"
                f"1. Precio de la consulta\n"
                f"2. Direcciones y ubicaciones\n"
                f"3. En qué consiste la consulta médica\n"
                f"4. Contactar directamente con el Dr. Guzmán\n"
                f"5. Agendar una cita")
    if any(w in t for w in ["hola", "buenos días", "buenas tardes", "buenas noches", "holi"]):
        return f"¡Hola! {get_time_greeting()} ¿En qué puedo ayudarle?"
    if any(w in t for w in ["gracias", "muchas gracias"]):
        return "Con gusto. ¿Hay algo más en lo que pueda ayudarle?"
    if any(w in t for w in SCHEDULE_KEYWORDS + REBOOK_KEYWORDS + CANCEL_KEYWORDS):
        return ("Claro. Puedo crear, mover o cancelar su cita. Dígame el día y hora (ej.: 'jueves 10 am') "
                "y la ciudad (Guayaquil/Milagro) por favor.")
    return ("Gracias por su mensaje. Puedo orientarle con información y ayudarle a coordinar una "
            "valoración cuando lo necesite. ¿Qué le gustaría consultar?")

def ana_reply(user_text: str, session: Dict, session_id: str) -> str:
    state = session.setdefault("state", {})
    state["last_message_time"] = datetime.now(TZ).isoformat()
    state["conversation_stage"] = "general"  # Actualiza según flujo

    urg = red_flag_guard(user_text)
    if urg:
        return urg

    # FAQs estratégicas (antes que otros flujos)
    faq = faq_flow(user_text)
    if faq:
        return faq

    r = pain_flow(user_text, state)
    if r:
        return r

    r = schedule_flow(user_text, state, session_id)
    if r:
        return r

    return generic_reply(user_text, state)

# ------------ Endpoints ------------
@app.get("/health")
def health():
    return {
        "status": "ok",
        "calendar_id": GOOGLE_CALENDAR_ID,
        "tz": str(TZ),
        "duration_min": APPT_DURATION_MIN,
        "scheduler": bool(SCHED),
        "telegram_ready": bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID),
        "whatsapp_ready": bool(WHATSAPP_TOKEN and WHATSAPP_PHONE_ID),
    }

@app.get("/appointments", response_model=List[Appointment])
def list_appointments():
    return APPOINTMENTS

@app.post("/chat", response_model=ChatOut)
def chat(inp: ChatIn) -> ChatOut:
    session = SESSIONS.setdefault(inp.session_id, {"history": [], "state": {}})
    session["history"].append({"role": "user", "content": inp.text})
    reply = ana_reply(inp.text, session, inp.session_id)
    session["history"].append({"role": "assistant", "content": reply})
    schedule_inactivity_reminder(inp.session_id)
    return ChatOut(reply=reply)

# ============================================================
# BLOQUE 2: /webhook (WhatsApp oficial)
# ============================================================
@app.get("/webhook")
async def whatsapp_webhook_verify(request: Request):
    # Verificación de Meta (GET)
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")
    if mode == "subscribe" and token == ANA_VERIFY:
        return Response(content=challenge, media_type="text/plain")
    return Response(status_code=403)

@app.post("/webhook")
async def whatsapp_webhook_receive(request: Request):
    """
    Procesa mensajes entrantes de WhatsApp:
    - Extrae 'from' y 'text'
    - Llama al flujo /chat con session_id estable 'wa:<from>'
    - Devuelve la respuesta al usuario por WhatsApp
    """
    try:
        payload = await request.json()
    except Exception:
        return {"status": "bad_json"}
    try:
        changes = payload["entry"][0]["changes"][0]["value"]
        # Mensajes (puede venir un array)
        msg = changes.get("messages", [])[0]
        from_id = msg["from"]  # ej: '5939XXXXXXX'
        text_in = (msg.get("text", {}) or {}).get("body", "") or ""
    except Exception:
        # Siempre 200 para no forzar reintentos infinitos de Meta
        return {"status": "ignored"}
    # Id de sesión estable por número
    session_id = f"wa:{from_id}"
    # Llama a TU flujo de chat (igual que pruebas en /docs)
    reply = chat_reply_via_http(session_id, text_in)
    # Responde por WhatsApp
    try:
        wa_send_text(from_id, reply)
    except Exception as e:
        # Evita que un fallo en el envío haga que Meta reintente sin parar
        pass
    # Responder 200 OK siempre a Meta
    return {"status": "ok"}

# ======================================================================
# BLOQUE 3: /whatsapp/webhook (ruta alternativa)
# ======================================================================
@app.get("/whatsapp/webhook")
async def whatsapp_webhook_verify_alt(request: Request):
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")
    if mode == "subscribe" and token == ANA_VERIFY:
        return Response(content=challenge, media_type="text/plain")
    return Response(status_code=403)

@app.post("/whatsapp/webhook")
async def whatsapp_webhook_receive_alt(request: Request):
    try:
        payload = await request.json()
    except Exception:
        return {"status": "bad_json"}
    try:
        changes = payload["entry"][0]["changes"][0]["value"]
        msg = changes.get("messages", [])[0]
        from_id = msg["from"]
        text_in = (msg.get("text", {}) or {}).get("body", "") or ""
    except Exception:
        return {"status": "ignored"}
    session_id = f"wa:{from_id}"
    reply = chat_reply_via_http(session_id, text_in)
    try:
        wa_send_text(from_id, reply)
    except Exception:
        pass
    return {"status": "ok"}
# ==========================
# TELEGRAM WEBHOOK OPCIONAL
# ==========================
from fastapi import Request


def tg_send(chat_id: str, text: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    requests.post(url, json={"chat_id": chat_id, "text": text}, timeout=15)

@app.post("/telegram/webhook")
async def telegram_webhook(request: Request):
    update = await request.json()
    msg = update.get("message") or {}
    chat_id = str(msg.get("chat", {}).get("id"))
    text_in = (msg.get("text") or "").strip()
    if not chat_id or not text_in:
        return {"ok": True}

    # Usa un session_id estable por usuario
    session_id = f"tg:{chat_id}"
    reply = chat_reply_via_http(session_id, text_in)

    # Enviar respuesta
    tg_send(chat_id, reply)
    return {"ok": True}