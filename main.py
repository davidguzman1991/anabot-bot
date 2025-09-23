
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
#   # Para WhatsApp Cloud API (opcional):
#   WHATSAPP_TOKEN=...
#   WHATSAPP_PHONE_ID=...
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

from fastapi import FastAPI
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
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")

WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN", "")
WHATSAPP_PHONE_ID = os.getenv("WHATSAPP_PHONE_ID", "")

# Horarios por sede (0=Lunes ... 6=Domingo).
WORKING_HOURS = {
    "Guayaquil": {
        0: [("09:00","13:00"), ("15:00","19:00")],
        1: [("09:00","13:00"), ("15:00","19:00")],
        2: [("09:00","13:00"), ("15:00","19:00")],
        3: [("09:00","13:00"), ("15:00","19:00")],
        4: [("09:00","13:00"), ("15:00","18:00")],  # viernes cierra 18:00
        5: [("09:00","13:00")],                     # sábado solo mañana
        6: []                                        # domingo cerrado
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

CLINIC_GYE = "Hospital de Especialidades de la ciudad, Torre Sur, consultorio 204 (antigua Clínica Kennedy Alborada)"
CLINIC_MILAGRO = "Clínica Santa Elena (Av. Cristóbal Colón y Gral. P. J. Montero), Milagro"
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
        url = f"https://graph.facebook.com/v17.0/{WHATSAPP_PHONE_ID}/messages"
        headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}", "Content-Type": "application/json"}
        payload = {
            "messaging_product": "whatsapp",
            "to": phone if phone.startswith("+") else f"+{phone}" if not phone.startswith("593") else phone,
            "type": "text",
            "text": {"body": message}
        }
        r = requests.post(url, json=payload, headers=headers, timeout=10)
        return r.ok
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
    "signos de acv", "acv", "ictus"
]

def red_flag_guard(text: str) -> Optional[str]:
    t = text.lower()
    if any(flag in t for flag in RED_FLAGS):
        return ("⚠️ Por su seguridad: los síntomas que menciona requieren valoración inmediata. "
                "Este canal no atiende emergencias. Por favor acuda al servicio de urgencias más cercano "
                "o comuníquese con los números de emergencia locales.")
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

    if awaiting == "name":
        name = extract_name(user_text) or user_text.strip().title()
        contact["name"] = name
        parts = name.split()
        apellido = parts[-1] if len(parts) >= 2 else name
        state["awaiting"] = "honorific"
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
        return "¿Me confirma un número de teléfono o WhatsApp para contactarle?"

    if awaiting == "phone":
        phone = extract_phone(user_text) or normalize_phone(user_text)
        if not phone or not valid_phone_ec(phone):
            return "Creo que ese número no es válido. ¿Podría escribirlo nuevamente? (Ej.: 09XXXXXXXX)"
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
        return "Para asistirle, ¿me indica su nombre y apellido? (Ej.: 'Me llamo Juan Pérez')"
    if not contact.get("honorific"):
        state["awaiting"] = "honorific"
        parts = contact["name"].split()
        apellido = parts[-1] if len(parts) >= 2 else contact["name"]
        return (f"¿Prefiere que me dirija como <b>Señor</b> o <b>Señora</b> {apellido}? "
                "(responda: señor / señora / señorita)")
    if not contact.get("phone"):
        state["awaiting"] = "phone"
        return "¿Me confirma un número de teléfono o WhatsApp para contactarle?"
    if contact.get("consent") is None:
        state["awaiting"] = "consent"
        return ("Para continuar, ¿autoriza el uso de sus datos con fines de agenda y comunicación médica? "
                "(responda: sí / no)")
    if not contact.get("preferred_channel"):
        state["awaiting"] = "channel"
        return ("¿Por qué canal prefiere que le contactemos? (WhatsApp / llamada / correo)")
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

def schedule_flow(user_text: str, state: Dict, session_id: str) -> Optional[str]:
    t = user_text.lower()

    # Recolección de datos en curso
    if state.get("awaiting") in {"name","honorific","phone","consent","channel"}:
        return schedule_contact_wizard(user_text, state, session_id)

    # Cancelación
    if any(k in t for k in CANCEL_KEYWORDS):
        last = next((a for a in reversed(APPOINTMENTS) if a.session_id == session_id and a.status=="scheduled"), None)
        if not last:
            return "No encuentro una cita activa para cancelar. ¿Podría indicarme la fecha aproximada?"
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
        return "¿Podría indicarme la nueva fecha y hora? (ej.: martes 10:00)"

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
                    f"Canal: {contact.get('preferred_channel','(no informado)')}")
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
            return (f"✅ ¡Listo! Le agendé para {format_dt_es(when)} en {where}.\n"
                    f"📍 {lugar}\n{cal_msg}\nℹ️ {ATT_NOTE}")

        if any(w in t for w in NO_WORDS):
            state.pop("pending_when")
            state.pop("pending_where", None)
            return "De acuerdo. Indíqueme otro día y hora que le vengan bien."

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
        return WELCOME
    if any(w in t for w in ["hola", "buenos días", "buenas tardes", "buenas noches", "holi"]):
        return "¡Hola! ¿En qué puedo ayudarle?"
    if any(w in t for w in ["gracias", "muchas gracias"]):
        return "Con gusto. ¿Hay algo más en lo que pueda ayudarle?"
    if any(w in t for w in SCHEDULE_KEYWORDS + REBOOK_KEYWORDS + CANCEL_KEYWORDS):
        return ("Claro. Puedo crear, mover o cancelar su cita. Dígame el día y hora (ej.: 'jueves 10 am') "
                "y la ciudad (Guayaquil/Milagro).")
    return ("Gracias por su mensaje. Puedo orientarle con información y ayudarle a coordinar una "
            "valoración cuando lo necesite. ¿Qué le gustaría consultar?")


def ana_choose_reply(user_text: str, session_state: Dict, session_id: str) -> str:
    """Orquesta el flujo de respuesta de ANA: semáforo, agenda, FAQs y genérico."""
    # 1) Seguridad primero
    rf = red_flag_guard(user_text)
    if rf:
        return rf
    # 2) Agenda (reagendar/cancelar/nueva)
    ans = schedule_flow(user_text, session_state, session_id)
    if ans:
        return ans
    # 3) FAQs cortas
    ans = faq_flow(user_text)
    if ans:
        return ans
    # 4) Genérico (bienvenida/small talk)
    return generic_reply(user_text, session_state)

def ana_reply(user_text: str, session: Dict, session_id: str) -> str:
    state = session.setdefault("state", {})

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
    return ChatOut(reply=reply)
# --- WhatsApp Cloud API: VERIFICACIÓN Y RECEPCIÓN WEBHOOK ---

from fastapi import Request, HTTPException
import os

# Debe existir en Railway como variable: ANA_VERIFY=ANA_CHATBOT  (o el valor que uses)
VERIFY_TOKEN = os.getenv("ANA_VERIFY", "ANA_CHATBOT")

def _verify_params(params: dict):
    """
    Meta llama con ?hub.mode=&hub.verify_token=&hub.challenge=
    Debemos devolver hub.challenge si el verify_token coincide.
    """
    mode = params.get("hub.mode")
    token = params.get("hub.verify_token")
    challenge = params.get("hub.challenge")

    if mode == "subscribe" and token == VERIFY_TOKEN and challenge is not None:
        # Meta acepta texto plano o número. Si es numérico, devuelve int.
        return int(challenge) if str(challenge).isdigit() else challenge
    # Si no coincide, 403 (Meta lo interpreta como verificación fallida)
    raise HTTPException(status_code=403, detail="Verification failed")

# Ruta corta que estás usando en Meta: /webhook
@app.get("/webhook")
async def whatsapp_webhook_verify(request: Request):
    params = dict(request.query_params)
    return _verify_params(params)

# Por si en algún momento usas el prefijo /whatsapp/webhook
@app.get("/whatsapp/webhook")
async def whatsapp_webhook_verify_alt(request: Request):
    params = dict(request.query_params)
    return _verify_params(params)

# Recepción de mensajes entrantes (Meta hace POST aquí)
@app.post("/webhook")
async def whatsapp_webhook_receive(request: Request):
    data = await request.json()
    try:
        entry = data.get("entry", [])
        if not entry:
            return {"status": "ok"}
        changes = entry[0].get("changes", [])
        if not changes:
            return {"status": "ok"}
        value = changes[0].get("value", {})
        msgs = value.get("messages", [])
        if not msgs:
            return {"status": "ok"}  # entregas/estados
        msg = msgs[0]
        wa_from = msg.get("from") or ""
        # texto según tipo
        text = ""
        t = msg.get("type")
        if t == "text":
            text = (msg.get("text") or {}).get("body", "")
        elif t == "button":
            text = (msg.get("button") or {}).get("text", "")
        elif t == "interactive":
            it = msg.get("interactive") or {}
            if it.get("type") == "list_reply":
                text = (it.get("list_reply") or {}).get("title", "") or (it.get("list_reply") or {}).get("id", "")
            elif it.get("type") == "button_reply":
                text = (it.get("button_reply") or {}).get("title", "") or (it.get("button_reply") or {}).get("id", "")
        # Sesión
        session = SESSIONS.setdefault(wa_from, {"state": {}, "history": []})
        state = session["state"]
        session_id = wa_from
        # Obtener respuesta
        reply = ana_choose_reply(text, state, session_id)
        # Enviar respuesta por WhatsApp
        if reply:
            notify_whatsapp(wa_from, reply)
            # Notifica a Telegram (operador) si está configurado
            try:
                notify_telegram(f"📩 WhatsApp de {wa_from}:
{text}

🤖 ANA respondió:
{reply}")
            except Exception:
                pass
    except Exception:
        pass
    return {"status": "ok"}

@app.post("/whatsapp/webhook")
async def whatsapp_webhook_receive_alt(request: Request):
    # Alias de /webhook
    return await whatsapp_webhook_receive(request)
