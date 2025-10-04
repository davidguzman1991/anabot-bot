# flow_engine.py
# ------------------------------------------------------------------------------
# Motor de flujo para AnaBot
# - Lee flow.json (menús y mensajes)
# - Maneja estados "ESPERANDO_*" del flujo
# - Integra lógica de BD (patients/appointments) usando db_utils.py
# - Incluye diccionario de intenciones y atajos 0/9
#
# Requisitos:
# - db_utils.py con funciones de pacientes/citas (provistas)
# - flow.json con los nodos/edges (tu versión consolidada de 0–5)
#
# Nota: Este engine no hace I/O de red. Expone funciones puras que puedes
# llamar desde tu webhook / bot framework.

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta
from typing import Any, Dict, Optional, Tuple

import db_utils

# ------------------------------------------------------------------------------
# Utilidades
# ------------------------------------------------------------------------------

def _load_flow(path: str = None) -> Dict[str, Any]:
    path = path or os.getenv("FLOW_JSON_PATH", "flow.json")
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    # normalización simple
    nodes = data.get("nodes") or data  # soporta { "nodes": {...} } o {...}
    return nodes

def _now_utc() -> datetime:
    # Si necesitas tz local, cámbialo aquí
    return datetime.utcnow()

def _normalize_text(s: str) -> str:
    s = s.lower().strip()
    # quitar tildes simples
    tildes = (("á", "a"), ("é", "e"), ("í", "i"), ("ó", "o"), ("ú", "u"), ("ñ", "ñ"))
    for a, b in tildes:
        s = s.replace(a, b)
    # colapsar espacios
    s = re.sub(r"\s+", " ", s)
    return s

# ------------------------------------------------------------------------------
# Diccionario de intenciones / atajos (menú principal)
# ------------------------------------------------------------------------------

INTENTS = {
    "servicios": {"keys": ["s", "se", "servicio", "servicios", "precio", "valor", "costo", "duracion",
                           "ecg", "electro", "nutricion", "plan", "neuropatia", "pie diabetico",
                           "guayaquil", "milagro", "direccion", "ubicacion", "mapa"]},
    "agendar":   {"keys": ["a", "ag", "agendar", "agenda", "cita", "sacar cita", "sacar turno", "turno",
                           "reservar", "reserva", "hacer cita", "programar", "pedir cita",
                           "asendar", "ajendar", "ajendarme"]},
    "reagendar": {"keys": ["r", "rg", "reagendar", "cambiar hora", "mover cita", "posponer",
                           "reprogramar", "modificar cita"]},
    "cancelar":  {"keys": ["cancelar", "anular", "borrar cita", "ya no", "no puedo ir", "suspender"]},
    "consultar": {"keys": ["c", "cc", "consultar", "ver cita", "tengo cita", "confirmar hora",
                           "a que hora es", "cuando es mi cita", "detalles de mi cita", "donde es mi cita"]},
    "hablar":    {"keys": ["h", "dr", "hablar con doctor", "hablar con el dr", "hablar con guzman",
                           "medico", "humano", "asesor", "whatsapp del doctor", "numero del dr",
                           "comunicarme con el doctor", "llamar al medico", "mensaje para el doctor"]},
    "inicio":    {"keys": ["9", "i", "in", "inicio", "menu", "comenzar", "empezar", "home"]},
    "atras":     {"keys": ["0", "b", "atr", "atras", "volver", "regresar", "retroceder"]},
}

def infer_intent(text: str) -> Optional[str]:
    t = _normalize_text(text)
    # números explícitos 1..5
    if re.fullmatch(r"[1-5]", t):
        return t  # devolvemos el mismo número para ruteo inmediato
    # comandos universales
    if t in INTENTS["inicio"]["keys"]:
        return "9"
    if t in INTENTS["atras"]["keys"]:
        return "0"
    # detectar intención por palabras clave
    for intent, cfg in INTENTS.items():
        if intent in ("inicio", "atras"):
            continue
        for k in cfg["keys"]:
            if k in t:
                return intent
    return None

# ------------------------------------------------------------------------------
# Validaciones de entradas (sin i18n para simplificar)
# ------------------------------------------------------------------------------

RE_DNI = re.compile(r"^[A-Za-z0-9]{8,20}$")         # cédula 10 dígitos o pasaporte alfanumérico
RE_DNI_ONLY_NUM = re.compile(r"^\d{10}$")           # exacto 10 números para cédula
RE_NAME = re.compile(r"^[A-Za-zÁÉÍÓÚáéíóúÑñ ]{5,60}$")
RE_DATE_DDMMYYYY = re.compile(r"^\d{2}[-/]\d{2}[-/]\d{4}$")
RE_PHONE = re.compile(r"^\d{8,12}$")
RE_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

def parse_birth_date(s: str) -> Optional[str]:
    """Devuelve 'YYYY-MM-DD' o None si no matchea."""
    s = s.strip()
    if RE_DATE_DDMMYYYY.match(s):
        d, m, y = re.split(r"[-/]", s)
        try:
            dt = datetime(int(y), int(m), int(d))
            return dt.strftime("%Y-%m-%d")
        except Exception:
            return None
    return None

def pick_date_from_option(option: str) -> datetime:
    """
    Convierte opción '1|2|3' a fecha base (hoy/mañana/otra).
    La hora por defecto será 10:00 local (ajústalo si usas otra TZ).
    """
    base = datetime.now()
    hour = 10
    minute = 0
    if option == "1":  # Hoy
        target = base
    elif option == "2":  # Mañana
        target = base + timedelta(days=1)
    else:
        target = base
    return target.replace(hour=hour, minute=minute, second=0, microsecond=0)

def combine_date_and_time(d: datetime, hhmm: Optional[str] = None) -> datetime:
    """Devuelve datetime con hh:mm (si no viene, toma 10:00)."""
    if hhmm:
        try:
            hh, mm = hhmm.split(":")
            return d.replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)
        except Exception:
            pass
    return d.replace(hour=10, minute=0, second=0, microsecond=0)

# ------------------------------------------------------------------------------
# Estados del flujo que requieren handler
# ------------------------------------------------------------------------------

STATE_WAIT_DNI = "ESPERANDO_CEDULA"
STATE_WAIT_NAME = "ESPERANDO_NOMBRE"
STATE_WAIT_BDATE = "ESPERANDO_FNAC"
STATE_WAIT_PHONE = "ESPERANDO_TEL"
STATE_WAIT_EMAIL = "ESPERANDO_EMAIL"
STATE_WAIT_DAY = "ESPERANDO_DIA"
STATE_WAIT_OTHER_DATE = "ESPERANDO_FECHA_OTRA"
STATE_WAIT_MESSAGE = "ESPERANDO_MENSAJE_DR"

# ------------------------------------------------------------------------------
# Contexto de sesión (típicamente guardado en tabla sessions.extra)
# ------------------------------------------------------------------------------

def ensure_context(session: Dict[str, Any]) -> Dict[str, Any]:
    ctx = session.get("extra") or {}
    if not isinstance(ctx, dict):
        ctx = {}
    session["extra"] = ctx
    return ctx

# ------------------------------------------------------------------------------
# Handlers principales por estado
# ------------------------------------------------------------------------------

def handle_wait_dni(user_input: str, session: Dict[str, Any]) -> Tuple[str, str]:
    """
    Primer paso de agendar/consultar/reagendar/cancelar: pedir DNI o pasaporte.
    Si existe paciente -> en Agendar: saltar a seleccionar día.
    Si no existe -> pasar a pedir nombre completo.
    Devuelve (next_state, reply_text)
    """
    t = _normalize_text(user_input).replace(" ", "")
    if not (RE_DNI_ONLY_NUM.match(t) or RE_DNI.match(t)):
        return STATE_WAIT_DNI, "El número ingresado no es válido 🤔. Ingresa tu **cédula (10 dígitos)** o **pasaporte alfanumérico**.\n0️⃣ Atrás · 9️⃣ Inicio"

    ctx = ensure_context(session)
    ctx["dni"] = t

    # lookup paciente
    patient = db_utils.get_patient_by_dni(t)
    if patient:
        # paciente existe → en flujo de agendar saltará a día; en consultar mostrará detalle
        ctx["patient_exists"] = True
        reply = "¡Hola, **{}**! 👋\nIndícame qué día deseas ser atendido:\n1️⃣ Hoy  2️⃣ Mañana  3️⃣ Otra fecha\n0️⃣ Atrás · 9️⃣ Inicio".format(patient.get("full_name", ""))
        return STATE_WAIT_DAY, reply

    # no existe: pedimos nombre completo
    ctx["patient_exists"] = False
    return STATE_WAIT_NAME, "No encontré tu registro 🗂️.\nEscribe tu **nombre y dos apellidos** (ej.: *María López García*)."

def handle_wait_name(user_input: str, session: Dict[str, Any]) -> Tuple[str, str]:
    t = user_input.strip()
    if not RE_NAME.match(t):
        return STATE_WAIT_NAME, "Parece que ese nombre no tiene el formato correcto 🤔.\nEscribe tu **nombre y apellido** o **nombre y dos apellidos**.\nEj.: *María López García*."
    ctx = ensure_context(session)
    ctx["full_name"] = t
    return STATE_WAIT_BDATE, "Gracias 🙏. Ahora escribe tu **fecha de nacimiento** en formato **DD–MM–AAAA** (ej.: *20–06–1991*) 📅."

def handle_wait_birthdate(user_input: str, session: Dict[str, Any]) -> Tuple[str, str]:
    birth = parse_birth_date(user_input)
    if not birth:
        return STATE_WAIT_BDATE, "Formato de fecha inválido 🤔. Usa **DD–MM–AAAA** (ej.: *20–06–1991*)."
    ctx = ensure_context(session)
    ctx["birth_date"] = birth
    return STATE_WAIT_PHONE, "Perfecto ✨. Ahora tu **número de contacto** (celular o WhatsApp). Ej.: *09xxxxxxxx* 📞."

def handle_wait_phone(user_input: str, session: Dict[str, Any]) -> Tuple[str, str]:
    t = re.sub(r"[^\d]", "", user_input)  # limpiar
    if not RE_PHONE.match(t):
        return STATE_WAIT_PHONE, "Ese teléfono parece incompleto 🤔. Envía solo dígitos (8–12)."
    ctx = ensure_context(session)
    ctx["phone_ec"] = t
    return STATE_WAIT_EMAIL, "Gracias 💙. Tu **correo electrónico** (ej.: *nombre@mail.com*). Si no tienes, escribe *ninguno* ✉️."

def handle_wait_email(user_input: str, session: Dict[str, Any]) -> Tuple[str, str]:
    t = _normalize_text(user_input)
    email = None if t in ("ninguno", "no tengo", "ningun") else user_input.strip()
    if email and not RE_EMAIL.match(email):
        return STATE_WAIT_EMAIL, "Ese correo no parece válido 🤔. Escribe uno como *nombre@mail.com* o escribe *ninguno*."
    ctx = ensure_context(session)
    ctx["email"] = email or "ninguno"

    # con esto ya podemos crear paciente
    dni = ctx.get("dni")
    created = db_utils.create_patient(
        dni=dni,
        full_name=ctx.get("full_name", ""),
        birth_date=ctx.get("birth_date"),
        phone_ec=ctx.get("phone_ec"),
        email=ctx.get("email"),
        wa_user_id=session.get("user_id"),
    )
    # siguiente: elegir día
    reply = "¡Listo, **{}**! 🙌 Ya te registré.\nIndícame qué día deseas ser atendido:\n1️⃣ Hoy  2️⃣ Mañana  3️⃣ Otra fecha\n0️⃣ Atrás · 9️⃣ Inicio".format(ctx.get("full_name", ""))
    return STATE_WAIT_DAY, reply

def handle_wait_day(user_input: str, session: Dict[str, Any]) -> Tuple[str, str]:
    t = _normalize_text(user_input)
    if t not in ("1", "2", "3"):
        return STATE_WAIT_DAY, "Elige una opción válida:\n1️⃣ Hoy  2️⃣ Mañana  3️⃣ Otra fecha\n0️⃣ Atrás · 9️⃣ Inicio"

    ctx = ensure_context(session)
    ctx["day_option"] = t
    if t in ("1", "2"):
        # construimos starts_at con hora por defecto 10:00
        base = pick_date_from_option(t)
        starts_at = base.strftime("%Y-%m-%d %H:%M:%S")
        ctx["starts_at"] = starts_at

        # default site: puedes inferir de contexto o preguntar en Sección 1 antes
        site = ctx.get("site") or ctx.get("preferred_site") or "Guayaquil"
        ctx["site"] = site

        # crea cita
        dni = ctx.get("dni")
        db_utils.save_appointment(patient_dni=dni, site=site, starts_at=starts_at)

        nice_date = base.strftime("%d-%m-%Y")
        nice_time = base.strftime("%H:%M")
        reply = f"✅ Tu cita fue **agendada**.\n📆 Fecha: {nice_date}  🕐 Hora: {nice_time}\n📍 Sede: {site}\nRecuerda llegar 10 minutos antes ⏱️.\n9️⃣ Inicio"
        return "MENU_PRINCIPAL", reply

    # “Otra fecha”: pedimos fecha exacta
    return STATE_WAIT_OTHER_DATE, "Perfecto 📆. Escribe la **fecha exacta** en formato **DD–MM–AAAA**."

def handle_wait_other_date(user_input: str, session: Dict[str, Any]) -> Tuple[str, str]:
    birth = parse_birth_date(user_input)
    if not birth:
        return STATE_WAIT_OTHER_DATE, "Formato inválido 🤔. Escribe la fecha como **DD–MM–AAAA**."

    # Combinar con una hora por defecto (10:00) o podrías pedir hora luego.
    dt = datetime.strptime(birth, "%Y-%m-%d")
    starts_at = dt.replace(hour=10, minute=0, second=0, microsecond=0)

    ctx = ensure_context(session)
    ctx["starts_at"] = starts_at.strftime("%Y-%m-%d %H:%M:%S")
    site = ctx.get("site") or ctx.get("preferred_site") or "Guayaquil"
    ctx["site"] = site

    dni = ctx.get("dni")
    db_utils.save_appointment(patient_dni=dni, site=site, starts_at=ctx["starts_at"])

    reply = f"✅ Tu cita fue **agendada**.\n📆 Fecha: {dt.strftime('%d-%m-%Y')}  🕐 Hora: 10:00\n📍 Sede: {site}\nRecuerda llegar 10 minutos antes ⏱️.\n9️⃣ Inicio"
    return "MENU_PRINCIPAL", reply

def handle_wait_message(user_input: str, session: Dict[str, Any]) -> Tuple[str, str]:
    # Aquí solo confirmamos recepción. Si tienes una tabla de mensajes, puedes guardarlo.
    msg = user_input.strip()
    if not msg or len(msg) < 3:
        return STATE_WAIT_MESSAGE, "No pude leer tu solicitud 🙈. Escribe tu **motivo** y te ayudo."
    # (Opcional) guardar en logs propios
    reply = ("✅ **Mensaje recibido.** Ya notifiqué al Dr. Guzmán 🙌\n"
             "¿Deseas **agendar una cita** ahora 📅?\n1️⃣ Sí, agendar   9️⃣ Inicio")
    return "MENU_PRINCIPAL", reply

# ------------------------------------------------------------------------------
# Consultas adicionales para Reagendar/Cancelar/Consultar
# (estas funciones puedes llamarlas desde tu router al entrar en la sección 3/4)
# ------------------------------------------------------------------------------

def consult_appointment_by_dni(dni: str) -> Optional[str]:
    ap = db_utils.get_active_appointment_by_dni(dni)
    if not ap:
        return None
    dt = ap["starts_at"]
    if isinstance(dt, str):
        try:
            # intenta parsear si viene como string
            dt_parsed = datetime.fromisoformat(dt.replace("Z", "+00:00"))
        except Exception:
            dt_parsed = None
    else:
        dt_parsed = dt

    nice_date = dt_parsed.strftime("%d-%m-%Y") if dt_parsed else str(dt)
    nice_time = dt_parsed.strftime("%H:%M") if dt_parsed else "—"
    site = ap.get("site", "—")
    # dirección puedes enriquecerla según sede si la mapeas en flow.json
    reply = (f"✨ **Tu cita activa**\n"
             f"📆 **Fecha:** {nice_date}\n"
             f"🕐 **Hora:** {nice_time}\n"
             f"📍 **Sede:** {site}\n\n"
             f"¿Deseas hacer algo más?\n1️⃣ Reagendar 📅   2️⃣ Cancelar ❌   9️⃣ Inicio")
    return reply

def cancel_appointment_by_dni(dni: str) -> str:
    upd = db_utils.cancel_appointment(dni)
    if upd and upd.get("id"):
        return ("Tu cita fue **cancelada** correctamente ❌\n"
                "Gracias por avisarnos 🙏. Si deseas agendar más adelante, "
                "escribe ‘cita’ o marca 2️⃣ 📅.\n9️⃣ Inicio")
    return "No encontré una cita activa para cancelar 🗂️. ¿Deseas **agendar** una nueva cita? 1️⃣ Sí, agendar  9️⃣ Inicio"

def reschedule_appointment_by_dni(dni: str, new_date_ddmmyyyy: str, site: Optional[str] = None) -> str:
    iso = parse_birth_date(new_date_ddmmyyyy)
    if not iso:
        return "Formato de fecha inválido 🤔. Usa **DD–MM–AAAA**."
    new_dt = datetime.strptime(iso, "%Y-%m-%d").replace(hour=10, minute=0, second=0, microsecond=0)
    res = db_utils.reschedule_appointment(dni=dni, new_starts_at=new_dt.strftime("%Y-%m-%d %H:%M:%S"), site=site)
    if res and res.get("id"):
        return (f"Tu cita fue **reagendada** para el {new_dt.strftime('%d-%m-%Y')} a las 10:00 ✅\n"
                "Gracias por tu puntualidad y confianza 💙.\n9️⃣ Inicio")
    return "No encontré una cita activa para reagendar 🗂️. ¿Deseas **agendar** una nueva cita? 1️⃣ Sí, agendar  9️⃣ Inicio"

# ------------------------------------------------------------------------------
# Router principal
# ------------------------------------------------------------------------------

def run(flow_nodes: Dict[str, Any], session: Dict[str, Any], user_input: str) -> Dict[str, Any]:
    """
    Punto de entrada del engine.
    - flow_nodes: dict cargado de flow.json
    - session: dict con estado de la sesión (user_id, platform, extra, current_state, etc.)
    - user_input: texto del usuario
    Devuelve: {"reply": str, "next_state": str, "session": session_actualizado}
    """
    ctx = ensure_context(session)
    text = user_input or ""
    norm = _normalize_text(text)

    # comandos universales: 9 inicio, 0 atras
    if norm in INTENTS["inicio"]["keys"] or norm == "9":
        session["current_state"] = "MENU_PRINCIPAL"
        return {"reply": flow_nodes.get("menu_principal", {}).get("reply", "Inicio."), "next_state": "MENU_PRINCIPAL", "session": session}
    if norm in INTENTS["atras"]["keys"] or norm == "0":
        # si guardas un history_state en ctx, puedes regresar. Aquí volvemos al menú.
        session["current_state"] = "MENU_PRINCIPAL"
        return {"reply": flow_nodes.get("menu_principal", {}).get("reply", "Inicio."), "next_state": "MENU_PRINCIPAL", "session": session}

    state = session.get("current_state") or "MENU_PRINCIPAL"

    # Handlers de estados de captura
    if state == STATE_WAIT_DNI:
        nxt, rep = handle_wait_dni(text, session)
        session["current_state"] = nxt
        return {"reply": rep, "next_state": nxt, "session": session}

    if state == STATE_WAIT_NAME:
        nxt, rep = handle_wait_name(text, session)
        session["current_state"] = nxt
        return {"reply": rep, "next_state": nxt, "session": session}

    if state == STATE_WAIT_BDATE:
        nxt, rep = handle_wait_birthdate(text, session)
        session["current_state"] = nxt
        return {"reply": rep, "next_state": nxt, "session": session}

    if state == STATE_WAIT_PHONE:
        nxt, rep = handle_wait_phone(text, session)
        session["current_state"] = nxt
        return {"reply": rep, "next_state": nxt, "session": session}

    if state == STATE_WAIT_EMAIL:
        nxt, rep = handle_wait_email(text, session)
        session["current_state"] = nxt
        return {"reply": rep, "next_state": nxt, "session": session}

    if state == STATE_WAIT_DAY:
        nxt, rep = handle_wait_day(text, session)
        session["current_state"] = nxt
        return {"reply": rep, "next_state": nxt, "session": session}

    if state == STATE_WAIT_OTHER_DATE:
        nxt, rep = handle_wait_other_date(text, session)
        session["current_state"] = nxt
        return {"reply": rep, "next_state": nxt, "session": session}

    if state == STATE_WAIT_MESSAGE:
        nxt, rep = handle_wait_message(text, session)
        session["current_state"] = nxt
        return {"reply": rep, "next_state": nxt, "session": session}

    # Si estamos en el menú, interpretamos intención para ruteo rápido
    if state == "MENU_PRINCIPAL":
        intent = infer_intent(text) or ""
        # ruteo por número
        if intent in ("1", "2", "3", "4", "5"):
            opt = intent
        else:
            # mapea intención textual a opción
            mapping = {
                "servicios": "1",
                "agendar": "2",
                "reagendar": "3",
                "cancelar": "3",   # dentro de la sección 3 eliges 2) cancelar
                "consultar": "4",
                "hablar": "5",
            }
            opt = mapping.get(intent or "", "")

        if opt == "1":
            return {"reply": flow_nodes.get("servicios", {}).get("reply", ""), "next_state": "SERVICIOS", "session": session}
        if opt == "2":
            session["current_state"] = STATE_WAIT_DNI
            return {"reply": "Para agendar tu cita 🩺, escribe tu **cédula (10 dígitos)** o **pasaporte**.", "next_state": STATE_WAIT_DNI, "session": session}
        if opt == "3":
            session["current_state"] = STATE_WAIT_DNI
            return {"reply": "Para modificar tu cita 🩺, escribe tu **cédula (10 dígitos)** o **pasaporte**.", "next_state": STATE_WAIT_DNI, "session": session}
        if opt == "4":
            session["current_state"] = STATE_WAIT_DNI
            return {"reply": "Para consultar tu cita 🔎, escribe tu **cédula (10 dígitos)** o **pasaporte**.", "next_state": STATE_WAIT_DNI, "session": session}
        if opt == "5":
            session["current_state"] = STATE_WAIT_MESSAGE
            return {"reply": ("✍️ **Déjame tu mensaje** (motivo, síntomas y desde cuándo).\n"
                              "En breve notificaré al **Dr. Guzmán**.\n\n"
                              "📞 WhatsApp directo del Dr.: **0962062122**\n"
                              "⏱️ Horario: Lun–Sáb 09:00–19:00\n"
                              "🚑 Si es **urgente**, acude a **emergencias**.\n\n"
                              "0️⃣ Atrás · 9️⃣ Inicio"),
                    "next_state": STATE_WAIT_MESSAGE, "session": session}

        # Si no hubo match, devolvemos el menú genérico
        return {"reply": flow_nodes.get("menu_principal", {}).get("reply", "Elige una opción 1–5."), "next_state": "MENU_PRINCIPAL", "session": session}

    # Fallback: si el estado no coincide con nada
    session["current_state"] = "MENU_PRINCIPAL"
    return {"reply": flow_nodes.get("menu_principal", {}).get("reply", "Inicio."), "next_state": "MENU_PRINCIPAL", "session": session}

