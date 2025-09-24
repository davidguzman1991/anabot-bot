# main.py — ANA (v6) — 2025-09-23
# -----------------------------------------------
# Cambios clave:
# - Webhooks (WhatsApp/Telegram) llaman DIRECTO a ana_reply (evita fallback).
# - Normalizador de tildes `norm()` para todas las comparaciones.
# - Menú 1–5 y “sí” contextual para arrancar el wizard de agenda.
# - Emojis y tono empático en respuestas clave.
# - Conjuntos YES/NO normalizados (con/sin tildes).
# - Reseteo de miss_counter SOLO desde ana_reply/schedule_flow (no en helpers).
# - Corrección de typo: “en {where}”.
# -----------------------------------------------

from __future__ import annotations
import os, re, unicodedata
from typing import Dict, List, Optional
from datetime import datetime, timedelta, time
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv
from dateparser import parse as dp_parse
import requests

# ========= Helpers visuales y normalización =========
E = {
    "wave": "👋", "spark": "✨", "heart": "💙", "warn": "⚠️", "ok": "✅",
    "clock": "⏰", "calendar": "📅", "pin": "📍", "money": "💵",
    "doc": "🩺", "chat": "💬", "phone": "📞", "hand": "🤝", "think": "🤔", "smile": "🙂",
}

def norm(s: str) -> str:
    """lower + elimina tildes para comparar robusto en español."""
    s = s.lower()
    s = unicodedata.normalize("NFD", s)
    return "".join(ch for ch in s if unicodedata.category(ch) != "Mn")

YES_WORDS_RAW = {"si","sí","claro","ok","de acuerdo","confirmo","correcto","esta bien","está bien"}
NO_WORDS_RAW  = {"no","cambiar","otra hora","otro dia","otro día","reagendar","prefiero no"}
YES_WORDS = {norm(w) for w in YES_WORDS_RAW}
NO_WORDS  = {norm(w) for w in NO_WORDS_RAW}

# ========= Configuración =========
load_dotenv()

TZ = ZoneInfo("America/Guayaquil")
PORT = os.getenv("PORT", "8080")
ANA_VERIFY = os.getenv("ANA_VERIFY", "ANA_CHATBOT")

WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN", "")
WHATSAPP_PHONE_ID = os.getenv("WHATSAPP_PHONE_ID", "")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

APPT_DURATION_MIN = int(os.getenv("APPT_DURATION_MIN", "45"))
GOOGLE_CALENDAR_ID = os.getenv("GOOGLE_CALENDAR_ID", "primary")
SCOPES = ["https://www.googleapis.com/auth/calendar"]

# token.json desde env (Railway)
GOOGLE_TOKEN_JSON = os.getenv("GOOGLE_TOKEN_JSON")
if GOOGLE_TOKEN_JSON and not os.path.exists("token.json"):
    try:
        with open("token.json", "w", encoding="utf-8") as f:
            f.write(GOOGLE_TOKEN_JSON.strip())
    except Exception as e:
        print("WARN token.json:", e)

# ========= App =========
app = FastAPI(title="ANA — Asistente Médico", version="6.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=True,
    allow_methods=["*"], allow_headers=["*"],
)

# ========= Datos en memoria =========
SESSIONS: Dict[str, Dict] = {}
APPOINTMENTS: List[dict] = []

# ========= Modelos =========
class ChatIn(BaseModel):
    session_id: str
    text: str

class ChatOut(BaseModel):
    reply: str

# ========= Utilidades generales =========
WEEKDAYS = ["lunes","martes","miércoles","jueves","viernes","sábado","domingo"]

def get_time_greeting():
    h = datetime.now(TZ).hour
    if 5 <= h < 12: return "¡Buenos días!"
    if 12 <= h < 18: return "¡Buenas tardes!"
    return "¡Buenas noches!"

def format_dt_es(dt: datetime) -> str:
    d = dt.astimezone(TZ)
    wd = WEEKDAYS[d.weekday()]
    return f"{wd} {d.day:02d}/{d.month:02d}/{d.year} a las {d.hour:02d}:{d.minute:02d}"

def parse_dt_es(text: str, ref: Optional[datetime] = None) -> Optional[datetime]:
    settings = {
        "PREFER_DATES_FROM": "future",
        "TIMEZONE": "America/Guayaquil",
        "RETURN_AS_TIMEZONE_AWARE": True
    }
    if ref is not None: settings["RELATIVE_BASE"] = ref
    dt = dp_parse(text, languages=["es"], settings=settings)
    if not dt: return None
    if not dt.tzinfo: dt = dt.replace(tzinfo=TZ)
    return dt.astimezone(TZ)

def hhmm_to_time(s: str) -> time:
    h, m = map(int, s.split(":")); return time(h, m, tzinfo=TZ)

# ========= Mensajería externa =========
def wa_send_text(to: str, body: str):
    if not (WHATSAPP_TOKEN and WHATSAPP_PHONE_ID): return
    url = f"https://graph.facebook.com/v19.0/{WHATSAPP_PHONE_ID}/messages"
    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}", "Content-Type": "application/json"}
    data = {"messaging_product": "whatsapp","to": to,"type":"text","text":{"body": (body or "")[:4096]}}
    try:
        requests.post(url, headers=headers, json=data, timeout=20).raise_for_status()
    except Exception: pass

def tg_send(chat_id: str, text: str):
    if not TELEGRAM_BOT_TOKEN: return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try: requests.post(url, json={"chat_id": chat_id, "text": text}, timeout=15)
    except Exception: pass

# ========= Calendario (ligero, tolerante a fallo) =========
def get_calendar_service():
    try:
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
        if not os.path.exists("token.json"):
            return None
        creds = Credentials.from_authorized_user_file("token.json", SCOPES)
        return build("calendar","v3",credentials=creds)
    except Exception:
        return None

def is_slot_free(start_dt: datetime, duration_min: int) -> Optional[bool]:
    svc = get_calendar_service()
    if not svc: return None
    end_dt = start_dt + timedelta(minutes=duration_min)
    body = {"timeMin": start_dt.isoformat(), "timeMax": end_dt.isoformat(), "items":[{"id":GOOGLE_CALENDAR_ID}]}
    try:
        res = svc.freebusy().query(body=body).execute()
        return len(res["calendars"][GOOGLE_CALENDAR_ID]["busy"]) == 0
    except Exception:
        return None

def create_calendar_event(start_dt: datetime, where: str, title: str, description: str) -> Optional[str]:
    svc = get_calendar_service()
    if not svc: return None
    end_dt = start_dt + timedelta(minutes=APPT_DURATION_MIN)
    location = "Hospital de Especialidades — Torre Sur, C.204 (Guayaquil)" if where.lower().startswith("g") \
               else "Clínica Santa Elena — Milagro"
    body = {
        "summary": title, "location": location, "description": description,
        "start": {"dateTime": start_dt.isoformat(),"timeZone":"America/Guayaquil"},
        "end":   {"dateTime": end_dt.isoformat(),  "timeZone":"America/Guayaquil"},
    }
    try:
        ev = svc.events().insert(calendarId=GOOGLE_CALENDAR_ID, body=body, sendUpdates="all").execute()
        return ev.get("id")
    except Exception:
        return None

# ========= Horarios por sede =========
WORKING_HOURS = {
    "Guayaquil": {
        0: [("08:00","12:00"), ("16:00","19:30")],
        1: [("08:00","12:00"), ("16:00","19:30")],
        2: [("08:00","12:00"), ("16:00","19:30")],
        3: [("08:00","12:00"), ("16:00","19:30")],
        4: [("08:00","12:00"), ("16:00","19:30")],
        5: [("09:00","16:00")],
        6: []
    },
    "Milagro": {
        0: [("10:00","16:00")],
        2: [("10:00","16:00")],
        4: [("10:00","16:00")],
    }
}
HOLIDAYS = set([])

def within_working_hours(dt: datetime, where: str) -> bool:
    d = dt.astimezone(TZ); day = d.weekday()
    if d.strftime("%Y-%m-%d") in HOLIDAYS: return False
    windows = WORKING_HOURS.get(where, {}).get(day, [])
    if not windows: return False
    tnow = d.time()
    for s,e in windows:
        if hhmm_to_time(s) <= tnow <= hhmm_to_time(e):
            end_ok = (d + timedelta(minutes=APPT_DURATION_MIN)).time() <= hhmm_to_time(e)
            if end_ok: return True
    return False

def next_open_slot(dt: datetime, where: str, step_min=15, days=30) -> Optional[datetime]:
    cur = dt.astimezone(TZ).replace(second=0, microsecond=0)
    for _ in range(int((days*24*60)/step_min)):
        if within_working_hours(cur, where): return cur
        cur += timedelta(minutes=step_min)
    return None

# ========= Extracción de datos =========
NAME_PAT  = re.compile(r"(?:me llamo|mi nombre es|soy)\s+([A-ZÁÉÍÓÚÑ][a-záéíóúñ]+(?:\s+[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+){0,2})", re.I)
PHONE_PAT = re.compile(r"(\+?\d[\d\-\s]{7,}\d)")

def extract_name(text: str) -> Optional[str]:
    m = NAME_PAT.search(text)
    if m: return m.group(1).strip().title()
    bare = text.strip()
    if len(bare.split()) in (1,2) and any(c.isalpha() for c in bare):
        if norm(bare) not in {"hola","gracias","cita","agendar","reservar"}:
            return bare.title()
    return None

def normalize_phone(raw: str) -> str: return re.sub(r"\D","",raw)
def valid_phone_ec(num: str) -> bool:
    if num.startswith("0") and len(num)==10 and num[1]=="9": return True
    if num.startswith("593") and len(num) in (11,12) and num[3]=="9": return True
    if num.startswith("09") and len(num)==10: return True
    return len(num) >= 9
def extract_phone(text: str) -> Optional[str]:
    m = PHONE_PAT.search(text);  return normalize_phone(m.group(1)) if m else None
def extract_channel(text: str) -> Optional[str]:
    t = norm(text)
    if "whatsapp" in t or "wasap" in t or "wpp" in t: return "WhatsApp"
    if "llamada" in t or "llamar" in t or "telefono" in t: return "Llamada"
    if "correo" in t or "mail" in t or "email" in t: return "Correo"
    return None

# ========= Respuestas base (helpers SIN tocar state dentro) =========
DOC_SUMMARY = ("El Dr. Guzmán es especialista en diabetes y sus complicaciones, "
               "con enfoque en mejorar la calidad de vida de los pacientes.")

def price_info() -> str:
    return (
        f"{E['money']} La consulta cuesta **$45** y dura ~60 minutos. "
        "Incluye: valoración nutricional con plan, educación diabetológica, examen de neuropatía, "
        "riesgo cardio-renal y EKG si hace falta."
        f"\n\n{E['calendar']} ¿Deseas **agendar**? *(sí/no)*"
    )

def location_info() -> str:
    return (
        f"{E['pin']} **Guayaquil:** Hospital de Especialidades (ant. Clínica Kennedy Alborada), Torre Sur, C.204. "
        "GPS: https://maps.app.goo.gl/7J8v9V9RJHfxADfz7"
        f"\n{E['pin']} **Milagro:** Clínica Santa Elena (Av. Cristóbal Colón y Gral. P. J. Montero). "
        "GPS: https://maps.app.goo.gl/sE2ehFSeDVWAQj867"
        f"\n\n{E['think']} ¿En **qué sede** deseas atenderte?"
    )

def consult_info() -> str:
    return (
        f"{E['doc']} En consulta revisamos tu historia, detectamos riesgos, ajustamos tratamiento, "
        "resolvemos dudas y te llevas un plan claro para mejorar tu calidad de vida."
        f"\n\n{E['calendar']} ¿Agendamos? *(sí/no)*"
    )

def contact_doctor_info() -> str:
    return (
        f"{E['phone']} Puedo **conectarte con el Dr. Guzmán** para un tema puntual. "
        "También puedes escribir/llamar al **0962062122**."
        f"\n\n{E['hand']} ¿Deseas que lo contacte ahora o prefieres **agendar** una consulta?"
    )

# ========= RED FLAGS =========
RED_FLAGS = [
    "dolor en el pecho","dolor toracico","dificultad para respirar","falta de aire",
    "convulsion","convulsiones","perdida de conciencia","desmayo",
    "hemorragia","sangrado abundante","fiebre alta","sepsis",
    "debilidad subita","cara caida","habla arrastrada","acv","ictus"
]
def red_flag_guard(user_text: str) -> Optional[str]:
    t = norm(user_text)
    if any(k in t for k in RED_FLAGS):
        return (f"{E['warn']} Lamento que estés pasando por esto. Has llegado al lugar correcto. "
                "El Dr. Guzmán puede ayudarte. ¿Qué deseas hacer?\n"
                f"1. {E['doc']} Más información sobre servicios\n"
                f"2. {E['calendar']} Agendar una consulta\n"
                f"3. {E['phone']} Conversar con el Dr. Guzmán")
    return None

# ========= FAQ flujo =========
def faq_flow(user_text: str) -> Optional[str]:
    t = norm(user_text)
    if "medicina" in t and ("natural" not in t and "alternativ" not in t):
        return (f"Soy Ana. {DOC_SUMMARY} Usamos **medicina basada en evidencia** {E['spark']}. ¿Te ayudo a agendar?")
    if "natural" in t or "alternativ" in t:
        return ("Trabajamos con **medicina tradicional basada en evidencia**; no usamos terapias alternativas. "
                "¿Deseas agendar una consulta?")
    if "iess" in t or "seguro social" in t or "seguro" in t:
        return ("Nuestros servicios son **privados**. "
                "Con gusto te doy opciones y agenda disponible.")
    if "quien es el dr" in t or "dr guzman" in t or "dr. guzman" in t:
        return (f"{DOC_SUMMARY} Atendemos en Guayaquil y Milagro. ¿Deseas agendar?")
    if any(k in t for k in ["precio","costo","cuanto cuesta","cuanto vale","valor"]):
        return price_info()
    if any(k in t for k in ["direccion","ubicacion","ubicado","donde queda","mapa"]):
        return location_info()
    if any(k in t for k in ["horario","hora","cuando atienden"]):
        return (f"Atención **previa cita**: Lun–Vie 8:00–12:00 y 16:00–19:30; Sáb 9:00–16:00. {E['calendar']} ¿Qué día te conviene?")
    if any(k in t for k in ["servicios","que ofrecen","consiste la consulta","consulta medica"]):
        return consult_info()
    return None

# ========= Dolor / empatía =========
PAIN_KEYWORDS = ["dolor","duele","ardor","hormigueo","punzante","quemazon","calambre","parestesias","quemazón"]
def pain_flow(user_text: str, state: Dict) -> Optional[str]:
    t = norm(user_text)
    if any(k in t for k in PAIN_KEYWORDS):
        if not state.get("asked_pain_scale"):
            state["asked_pain_scale"] = True
            return (f"{E['warn']} Lamento que estés con dolor. "
                    "Para entender mejor: del **1 al 10**, ¿cuánto te duele y **en qué zona**?")
        if not state.get("asked_since_when"):
            state["asked_since_when"] = True
            return (f"{E['think']} Gracias. ¿Desde **cuándo** lo sientes y qué lo **empeora o alivia**? "
                    "¿Tomaste algo que ayude?")
        return (f"{E['hand']} Con esa información podré guiarte mejor. "
                f"Si deseas, puedo {E['calendar']} **agendar** tu valoración ahora mismo.")
    return None

# ========= Wizard de agenda =========
def schedule_contact_wizard(user_text: str, state: Dict, session_id: str) -> Optional[str]:
    contact = state.setdefault("contact", {})
    awaiting = state.get("awaiting")

    if not state.get("pending_where") and awaiting != "where":
        state["awaiting"] = "where"
        return f"{E['pin']} ¿En **qué sede** deseas atenderte? *(Guayaquil o Milagro)*"

    if awaiting == "where":
        t = norm(user_text)
        where = "Guayaquil" if "guayaquil" in t else "Milagro" if "milagro" in t else None
        if where:
            state["pending_where"] = where
            state["awaiting"] = "name"
            return f"{E['hand']} Entendido, en **{where}**. ¿Tu **nombre y apellido**, por favor?"
        return "Por favor, indica **Guayaquil** o **Milagro**."

    if awaiting == "name":
        name = extract_name(user_text) or user_text.strip().title()
        contact["name"] = name
        state["awaiting"] = "cedula"
        return f"{E['think']} Gracias. ¿Tu **número de cédula**, por favor?"

    if awaiting == "cedula":
        contact["cedula"] = user_text.strip()
        state["awaiting"] = "birthdate"
        return f"{E['calendar']} ¿Tu **fecha de nacimiento**? *(ej.: 15/05/1980)*"

    if awaiting == "birthdate":
        birth = dp_parse(user_text, languages=["es"])
        if birth:
            contact["birthdate"] = birth.date().isoformat()
            state["awaiting"] = "email"
            return f"{E['chat']} ¿Tu **correo electrónico**? *(si no tienes, escribe 'no')*"
        return "Por favor, indícame en formato **día/mes/año**."

    if awaiting == "email":
        contact["email"] = None if "no" in norm(user_text) else user_text.strip()
        state["awaiting"] = "honorific"
        parts = contact["name"].split()
        apellido = parts[-1] if len(parts)>=2 else contact["name"]
        return f"{E['smile']} ¿Prefieres que te llame **Señor / Señora / Señorita** {apellido}?"

    if awaiting == "honorific":
        t = norm(user_text)
        contact["honorific"] = "Señorita" if "senorita" in t else "Señora" if "senora" in t or "sra" in t else "Señor"
        state["awaiting"] = "phone"
        return f"{E['phone']} ¿Tu **número de teléfono o WhatsApp**? *(Ej.: 09XXXXXXXX)*"

    if awaiting == "phone":
        phone = extract_phone(user_text) or normalize_phone(user_text)
        if not phone or not valid_phone_ec(phone):
            return "Creo que ese número no es válido. ¿Podrías escribirlo nuevamente? (Ej.: 09XXXXXXXX)"
        contact["phone"] = phone if phone.startswith("593") else ("593" + phone.lstrip("0"))
        state["awaiting"] = "consent"
        return f"{E['hand']} ¿Autorizas el uso de tus datos para **agenda y comunicación médica**? *(sí/no)*"

    if awaiting == "consent":
        t = norm(user_text)
        contact["consent"] = any(w in t for w in YES_WORDS)
        if not contact["consent"]:
            return ("Entiendo. Sin autorización no puedo finalizar la agenda. "
                    "Si cambias de opinión, indícame **sí**.")
        state["awaiting"] = "channel"
        return f"{E['chat']} ¿Por qué canal prefieres que te contactemos? *(WhatsApp / llamada / correo)*"

    if awaiting == "channel":
        contact["preferred_channel"] = extract_channel(user_text) or user_text.strip().title()
        state.pop("awaiting", None)
        state["contact_ready"] = True
        return f"{E['calendar']} Perfecto. Ya tengo tus datos. ¿Deseas que **confirme** la cita ahora? *(sí/no)*"

    if not contact.get("name"):
        state["awaiting"] = "name"
        return f"Para continuar, ¿tu **nombre y apellido**, por favor?"
    state["contact_ready"] = True
    return None

# ========= Flujo de agendamiento y cambios =========
SCHEDULE_KEYWORDS = ["cita","agendar","agenda","reservar","turno","agendame","agéndame"]
REBOOK_KEYWORDS    = ["cambiar","reagendar","mover","posponer","modificar"]
CANCEL_KEYWORDS    = ["cancelar","anular","eliminar la cita"]

def suggest_alternatives(start_dt: datetime, where: str, n=3) -> List[datetime]:
    res, cand, steps = [], next_open_slot(start_dt, where) or start_dt, 0
    while len(res)<n and steps<200:
        if within_working_hours(cand, where):
            ok = is_slot_free(cand, APPT_DURATION_MIN)
            if ok is None or ok: res.append(cand)
        cand += timedelta(minutes=15); steps += 1
    return res

def schedule_flow(user_text: str, state: Dict, session_id: str) -> Optional[str]:
    t = norm(user_text)

    if state.get("awaiting") in {"where","name","cedula","birthdate","email","honorific","phone","consent","channel"}:
        return schedule_contact_wizard(user_text, state, session_id)

    # cancelar
    if any(k in t for k in CANCEL_KEYWORDS):
        last = next((a for a in reversed(APPOINTMENTS) if a["session_id"]==session_id and a["status"]=="scheduled"), None)
        if not last:
            return "No encuentro una cita activa para cancelar. ¿Me indicas la fecha aproximada?"
        state["pending_cancel"] = last
        return f"¿Deseas **cancelar** tu cita del {format_dt_es(datetime.fromisoformat(last['when_iso']))} en {last['where']}? *(sí/no)*"

    if "pending_cancel" in state:
        if any(w in t for w in YES_WORDS):
            last = state.pop("pending_cancel")
            last["status"] = "canceled"
            # si hay Google Calendar, podrías borrar aquí (omitido por brevedad)
            return f"{E['ok']} Cita **cancelada**. ¿Deseas agendar una **nueva**?"
        if any(w in t for w in NO_WORDS):
            state.pop("pending_cancel", None)
            return "De acuerdo, mantenemos tu cita. ¿En qué más te ayudo?"
        return "¿Confirmas la cancelación? *(sí/no)*"

    # reagendar
    if any(k in t for k in REBOOK_KEYWORDS):
        new_dt = parse_dt_es(user_text)
        if not new_dt:
            state["rebook_intent"] = True
            return "Entiendo. ¿A qué **día y hora** deseas mover tu cita? (ej.: viernes 16:30)"
        state["pending_rebook_when"] = new_dt
        return f"¿Deseas mover tu cita a **{format_dt_es(new_dt)}**? *(sí/no)*"

    if state.get("rebook_intent"):
        new_dt = parse_dt_es(user_text)
        if new_dt:
            state["pending_rebook_when"] = new_dt
            state.pop("rebook_intent", None)
            return f"¿Deseas mover tu cita a **{format_dt_es(new_dt)}**? *(sí/no)*"
        return "¿Podrías indicar la nueva **fecha y hora**? (ej.: martes 10:00)"

    if "pending_rebook_when" in state:
        if any(w in t for w in YES_WORDS):
            last = next((a for a in reversed(APPOINTMENTS) if a["session_id"]==session_id and a["status"]=="scheduled"), None)
            if not last:
                state.pop("pending_rebook_when", None)
                return "No encuentro una cita activa para mover. ¿Creamos una nueva?"
            new_start = state.pop("pending_rebook_when")
            where = last["where"]
            if not within_working_hours(new_start, where):
                nxt = next_open_slot(new_start, where)
                return (f"Ese horario queda fuera de atención. ¿Te sirve **{format_dt_es(nxt)}**?" if nxt
                        else "No encontré horario cercano disponible. Indícame otra hora, por favor.")
            ok = is_slot_free(new_start, APPT_DURATION_MIN)
            if ok is False:
                alts = suggest_alternatives(new_start, where)
                s = "; ".join(format_dt_es(a) for a in alts) if alts else "otro horario"
                return f"Esa hora está ocupada. Te propongo: {s}. ¿Cuál prefieres?"
            # mover (si hubieras guardado event_id, aquí llamarías a update)
            last["when_iso"] = new_start.isoformat()
            return f"{E['ok']} Reagendé tu cita a **{format_dt_es(new_start)}** en **{where}**."
        if any(w in t for w in NO_WORDS):
            state.pop("pending_rebook_when", None)
            return "Perfecto, mantenemos tu cita actual. ¿Deseas otra cosa?"
        return "¿Confirmas el cambio de horario? *(sí/no)*"

    # crear nueva
    if "pending_when" in state:
        if any(w in t for w in YES_WORDS):
            # pedir datos si falta
            msg = schedule_contact_wizard("", state, session_id)
            if not state.get("contact_ready"):
                state["pending_confirmed"] = True
                return msg

            when: datetime = state.pop("pending_when")
            where = state.pop("pending_where","Guayaquil")

            if not within_working_hours(when, where):
                nxt = next_open_slot(when, where)
                if nxt:
                    state["pending_when"], state["pending_where"] = nxt, where
                    return f"Ese horario está fuera de atención. ¿Te sirve **{format_dt_es(nxt)}**? *(sí/no)*"
                return "No encontré horario cercano disponible. Indícame otra hora por favor."

            ok = is_slot_free(when, APPT_DURATION_MIN)
            if ok is False:
                alts = suggest_alternatives(when, where)
                s = "; ".join(format_dt_es(a) for a in alts) if alts else "otro horario"
                return f"Esa hora está ocupada. ¿Te sirve {s}?"

            contact = state.get("contact", {})
            honor = contact.get("honorific","Señor/a")
            name  = contact.get("name","")
            desc = (f"Cita programada por ANA. Sesión: {session_id}.\n"
                    f"Paciente: {honor} {name}\n"
                    f"Tel: {contact.get('phone','(no informado)')}\n"
                    f"Cédula: {contact.get('cedula','(no informado)')}\n"
                    f"Nacimiento: {contact.get('birthdate','(no informado)')}\n"
                    f"Canal: {contact.get('preferred_channel','(no informado)')}\n")

            ev_id = create_calendar_event(when, where, "Consulta — Unidad Médica", desc)
            APPOINTMENTS.append({
                "session_id":session_id, "when_iso":when.isoformat(),
                "where":where, "event_id":ev_id, "status":"scheduled",
                "created_at": datetime.now(TZ).isoformat()
            })
            lugar = ("Hospital de Especialidades — Torre Sur, C.204 (Guayaquil)"
                     if where.lower().startswith("g") else
                     "Clínica Santa Elena (Milagro)")
            cal_msg = "Agregada al Google Calendar." if ev_id else "No se pudo agregar al Calendar."
            return (f"{E['ok']} ¡Listo! Agendé para **{format_dt_es(when)}** en **{where}**.\n"
                    f"{E['pin']} {lugar}\n{E['calendar']} {cal_msg}\n"
                    f"{E['clock']} Recibirás recordatorios **24 h** y **2 h** antes.\n"
                    "Si necesitas mover o cancelar, avísame por aquí.")
        if any(w in t for w in NO_WORDS):
            state.pop("pending_when", None); state.pop("pending_where", None)
            return "De acuerdo. Indícame otro **día y hora** que te vengan bien."
        new_dt = parse_dt_es(user_text)
        if new_dt:
            state["pending_when"] = new_dt
            return f"¿Confirmo **{format_dt_es(new_dt)}**? *(sí/no)*"
        return "¿Confirmas la fecha/hora propuesta? *(sí/no)* o indícame otra fecha."

    if any(k in t for k in SCHEDULE_KEYWORDS):
        where = "Milagro" if "milagro" in t else "Guayaquil"
        state["pending_where"] = where
        dt = parse_dt_es(user_text)
        if not dt:
            return (f"Con gusto {E['calendar']}. Dime **día y hora** (ej.: 'jueves 10:00', '26/09 15:00') "
                    "y la **ciudad** (Guayaquil/Milagro).")
        state["pending_when"] = dt
        return f"¿Te reservo **{format_dt_es(dt)}** en **{where}**? *(sí/no)*"

    return None

# ========= Respuesta genérica =========
def generic_reply(user_text: str, state: Dict) -> str:
    t = norm(user_text)
    if not state.get("welcomed"):
        state["welcomed"] = True
        greeting = get_time_greeting()
        return (
            f"{greeting} {E['wave']} Soy **Ana**, asistente del Dr. Guzmán. "
            "Estoy aquí para ayudarte con información y agendar tu cita. "
            f"{E['spark']}\n\nElige una opción (escribe el **número**):\n"
            f"1. {E['money']} Precio de la consulta\n"
            f"2. {E['pin']} Direcciones y ubicaciones\n"
            f"3. {E['doc']} ¿En qué consiste la consulta?\n"
            f"4. {E['phone']} Contactar al Dr. Guzmán\n"
            f"5. {E['calendar']} Agendar una **cita**"
        )
    if any(w in t for w in ["hola","buenos dias","buenas tardes","buenas noches","holi"]):
        return f"{E['smile']} ¡Hola! ¿En qué puedo ayudarte?"
    if any(w in t for w in ["gracias","muchas gracias"]):
        return "Con gusto. ¿Hay algo más en lo que pueda ayudarte?"
    if any(k in t for k in SCHEDULE_KEYWORDS):
        return (f"Claro {E['calendar']}. Puedo crear o mover tu cita. "
                "Dime **día y hora** y la **ciudad** (Guayaquil/Milagro).")
    # fallback amable con CTA
    state["miss_counter"] = state.get("miss_counter", 0) + 1
    if state["miss_counter"] >= 2:
        state["miss_counter"] = 0
        return (f"{E['think']} No estoy segura de haber entendido. "
                "¿Quieres que te comparta el **menú** otra vez o prefieres **agendar**? "
                f"\n{E['spark']} Escribe **1–5** o 'agendar'.")
    return ("Gracias por tu mensaje. Puedo orientarte y ayudarte a coordinar una valoración. "
            "¿Sobre qué tema te gustaría consultar?")

# ========= Orquestador =========
def ana_reply(user_text: str, session: Dict, session_id: str) -> str:
    state = session.setdefault("state", {})
    state["last_message_time"] = datetime.now(TZ).isoformat()
    state["conversation_stage"] = "general"

    # Router 1–5 y “sí” contextual
    t_raw = user_text.strip()
    t = norm(t_raw)
    if t in {"1","2","3","4","5"}:
        state["last_menu"] = t
        state["miss_counter"] = 0
        if t == "1":
            state["awaiting_after_price"] = True;  return price_info()
        if t == "2":
            return location_info()
        if t == "3":
            state["awaiting_after_consult"] = True;  return consult_info()
        if t == "4":
            return contact_doctor_info()
        if t == "5":
            return schedule_contact_wizard("", state, session_id)

    if (t in YES_WORDS) and (state.get("awaiting_after_price") or state.get("awaiting_after_consult")):
        state.pop("awaiting_after_price", None); state.pop("awaiting_after_consult", None)
        state["miss_counter"] = 0
        return schedule_contact_wizard("", state, session_id)

    # Red flags
    urg = red_flag_guard(user_text)
    if urg: state["miss_counter"]=0;  return urg

    # FAQ
    faq = faq_flow(user_text)
    if faq: state["miss_counter"]=0;  return faq

    # Dolor
    r = pain_flow(user_text, state)
    if r: state["miss_counter"]=0;  return r

    # Agenda
    r = schedule_flow(user_text, state, session_id)
    if r: state["miss_counter"]=0;  return r

    # Genérico
    return generic_reply(user_text, state)

# ========= Endpoints =========
@app.get("/health")
def health():
    return {"status":"ok","tz":str(TZ),"duration_min":APPT_DURATION_MIN}

@app.post("/chat", response_model=ChatOut)
def chat(inp: ChatIn) -> ChatOut:
    session = SESSIONS.setdefault(inp.session_id, {"history": [], "state": {}})
    session["history"].append({"role":"user","content":inp.text})
    reply = ana_reply(inp.text, session, inp.session_id)
    session["history"].append({"role":"assistant","content":reply})
    return ChatOut(reply=reply)

# --- WhatsApp webhook (verificación) ---
@app.get("/webhook")
async def whatsapp_webhook_verify(request: Request):
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")
    if mode == "subscribe" and token == ANA_VERIFY:
        return Response(content=challenge, media_type="text/plain")
    return Response(status_code=403)

# --- WhatsApp webhook (mensajes) ---
@app.post("/webhook")
async def whatsapp_webhook_receive(request: Request):
    try:
        payload = await request.json()
    except Exception:
        return {"status":"bad_json"}
    try:
        changes = payload["entry"][0]["changes"][0]["value"]
        msg = changes.get("messages", [])[0]
        from_id = msg["from"]
        text_in = (msg.get("text", {}) or {}).get("body", "") or ""
    except Exception:
        return {"status":"ignored"}

    if not text_in:
        wa_send_text(from_id, "Recibí tu mensaje. Por ahora solo puedo leer **texto**. ¿Podrías escribirlo? 🙂")
        return {"status":"ok"}

    session_id = f"wa:{from_id}"
    session = SESSIONS.setdefault(session_id, {"history": [], "state": {}})
    session["history"].append({"role":"user","content":text_in})
    reply = ana_reply(text_in, session, session_id)
    session["history"].append({"role":"assistant","content":reply})

    wa_send_text(from_id, reply)
    return {"status":"ok"}

# --- Telegram webhook ---
@app.post("/telegram/webhook")
async def telegram_webhook(request: Request):
    update = await request.json()
    msg = update.get("message") or {}
    chat_id = str(msg.get("chat",{}).get("id"))
    text_in = (msg.get("text") or "").strip()
    if not chat_id:
        return {"ok": True}
    if not text_in:
        tg_send(chat_id, "Por ahora solo puedo leer **texto**. ¿Podrías escribirlo? 🙂")
        return {"ok": True}

    session_id = f"tg:{chat_id}"
    session = SESSIONS.setdefault(session_id, {"history": [], "state": {}})
    session["history"].append({"role":"user","content":text_in})
    reply = ana_reply(text_in, session, session_id)
    session["history"].append({"role":"assistant","content":reply})

    tg_send(chat_id, reply)
    return {"ok": True}

# --- Procfile recomendado ---
# web: uvicorn main:app --host 0.0.0.0 --port ${PORT:-8080}
