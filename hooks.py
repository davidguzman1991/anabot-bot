"""Business hooks for AnaBot flow v6."""

# --- Helper para forzar menú principal y limpiar sesión ---
def reset_to_main(session):
    session["state"] = "MENU_PRINCIPAL"
    session["step"] = None
    session.pop("en_proceso", None)
    session.pop("flags", None)
    saludo = get_daypart_greeting()
    menu = format_main_menu()
    return f"{saludo} 👋 Soy Ana, asistente virtual del Dr. David Guzmán.\n\n{menu}"

# Alias retrocompatibles y helpers exportados
def restablecer_a_principal(session):
    return reset_to_main(session)

def obtener_saludo_de_parte_del_dia(hora: int = None) -> str:
    return get_daypart_greeting(hora)

def es_saludo(texto: str) -> bool:
    return is_greeting(texto)

def formato_menu_principal() -> str:
    return format_main_menu()

# Red flag helper retrocompatible
def es_bandera_roja(texto: str) -> bool:
    # Usar la misma lógica que is_red_flag (alias)
    texto = (texto or "").lower()
    terms = ["quemazón", "hormigueo", "descargas eléctricas", "dolor", "frialdad", "calentura", "fiebre", "herida"]
    return any(term in texto for term in terms)

def is_red_flag(texto: str) -> bool:
    return es_bandera_roja(texto)

# __all__ para exportar helpers
__all__ = [
    "get_daypart_greeting", "obtener_saludo_de_parte_del_dia",
    "is_greeting", "es_saludo",
    "format_main_menu", "formato_menu_principal",
    "reset_to_main", "restablecer_a_principal",
    "es_bandera_roja", "is_red_flag"
]


"""Business hooks for AnaBot flow v6."""

import logging
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any, Dict, List, Optional, Tuple

import psycopg2
from psycopg2.extras import RealDictCursor
from zoneinfo import ZoneInfo

from config import get_settings
import db_utils

logger = logging.getLogger("hooks")
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s"))
    logger.addHandler(handler)
logger.setLevel(logging.INFO)

_SETTINGS = get_settings()
_DATABASE_URL = _SETTINGS.DATABASE_URL

TZ_LOCAL = ZoneInfo("America/Guayaquil")
TZ_UTC = ZoneInfo("UTC")
SLOT_MINUTES_FALLBACK = 45
GAP_MINUTES_FALLBACK = 15

SITE_LABELS = {
    "GYE": "Guayaquil",
    "MIL": "Milagro",
}

RED_FLAG_TERMS = [
    "quemazón", "hormigueo", "descargas eléctricas", "dolor", "frialdad", "calentura", "fiebre", "herida"
]


# --- Greeting/menu helpers actualizados ---
import re
from datetime import datetime
from zoneinfo import ZoneInfo

GREETING_TERMS = {"hola", "holi", "buenas", "buenos dias", "buenos días", "buenas tardes", "buenas noches", "hey", "qué tal", "que tal"}

def normalize_text(text: str) -> str:
    t = text or ""
    t = t.lower().strip()
    t = re.sub(r"\s+", " ", t)
    t = t.replace("á","a").replace("é","e").replace("í","i").replace("ó","o").replace("ú","u")
    return t

def is_greeting(text: str) -> bool:
    t = normalize_text(text)
    return any(term in t for term in GREETING_TERMS)

def get_daypart_greeting(hour: int = None) -> str:
    if hour is None:
        try:
            hour = datetime.now(tz=ZoneInfo("America/Guayaquil")).hour
        except Exception:
            hour = datetime.now().hour
    if hour < 12:
        return "¡Buenos días 🌞!"
    if 12 <= hour < 18:
        return "¡Buenas tardes ☀️!"
    return "¡Buenas noches 🌙!"

def format_main_menu() -> str:
    return (
        "Soy Ana 🤖, asistente virtual del Dr. David Guzmán. ¿Cómo te ayudo hoy?  \n\n"
        "1️⃣ Más información de servicios médicos  \n"
        "2️⃣ Agendar cita médica  \n"
        "3️⃣ Reagendar o cancelar  \n"
        "4️⃣ Consultar cita médica  \n"
        "5️⃣ Hablar con el Dr. Guzmán  \n\n"
        "ℹ️ En cualquier momento puedes usar:  \n"
        "0️⃣ Atrás · 9️⃣ Inicio"
    )

# Alias for test compatibility
RED_FLAG_TERMS = RED_FLAG_TERMS

GYE_WINDOWS: Dict[int, List[Tuple[time, time]]] = {
    0: [(time(9, 0), time(12, 0)), (time(16, 0), time(20, 0))],
    1: [(time(9, 0), time(12, 0)), (time(16, 0), time(20, 0))],
    2: [(time(9, 0), time(12, 0)), (time(16, 0), time(20, 0))],
    3: [(time(9, 0), time(12, 0)), (time(16, 0), time(20, 0))],
    4: [(time(9, 0), time(12, 0)), (time(16, 0), time(20, 0))],
    5: [(time(9, 0), time(16, 0))],
    6: [],
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _normalize(text: str) -> str:
    text = text or ""
    text = text.lower()
    normalized = unicodedata.normalize("NFD", text)
    return "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn")


def _conn():
    if not _DATABASE_URL:
        raise RuntimeError("DATABASE_URL is required")
    return psycopg2.connect(_DATABASE_URL)


def _now_local() -> datetime:
    return datetime.now(tz=TZ_LOCAL)


def _parse_date(date_str: str) -> date:
    return datetime.strptime(date_str, "%d-%m-%Y").date()


def _parse_datetime_local(label: str) -> datetime:
    return datetime.strptime(label, "%d-%m-%Y %H:%M").replace(tzinfo=TZ_LOCAL)


def _local_bounds(day: date) -> Tuple[datetime, datetime]:
    start = datetime.combine(day, time.min).replace(tzinfo=TZ_LOCAL)
    end = datetime.combine(day, time.max).replace(tzinfo=TZ_LOCAL)
    return start.astimezone(TZ_UTC), (end + timedelta(seconds=1)).astimezone(TZ_UTC)


def _slot_conflicts(candidate: datetime, existing: List[datetime], slot_minutes: int, gap_minutes: int) -> bool:
    span = timedelta(minutes=slot_minutes)
    gap = timedelta(minutes=gap_minutes)
    cand_start = candidate
    cand_end = candidate + span
    start_buffer = cand_start - gap
    end_buffer = cand_end + gap
    for booked in existing:
        booked_start = booked
        booked_end = booked + span
        booked_start_buffer = booked_start - gap
        booked_end_buffer = booked_end + gap
        if not (end_buffer <= booked_start_buffer or booked_end_buffer <= start_buffer):
            return True
    return False


def _generate_candidates(day: date, slot_minutes: int, gap_minutes: int) -> List[datetime]:
    windows = GYE_WINDOWS.get(day.weekday(), [])
    candidates: List[datetime] = []
    span = timedelta(minutes=slot_minutes)
    step = timedelta(minutes=slot_minutes + gap_minutes)
    for start_time, end_time in windows:
        current = datetime.combine(day, start_time).replace(tzinfo=TZ_LOCAL)
        window_end = datetime.combine(day, end_time).replace(tzinfo=TZ_LOCAL)
        while current + span <= window_end:
            candidates.append(current)
            current += step
    return candidates


def _site_label(code: str) -> str:
    return SITE_LABELS.get((code or "").upper(), code)


# ---------------------------------------------------------------------------
# Hooks implementation
# ---------------------------------------------------------------------------

@dataclass
class Hooks:
    # --- Router principal: fuerza menú y evita duplicados ---
    def route_input(self, session, text):
        from hooks import is_greeting, reset_to_main
        t = (text or "").strip()
        if session.get("is_new") or is_greeting(t) or not session.get("state") or session.get("state") not in {"MENU_PRINCIPAL", "RF_RED_FLAG"}:
            return reset_to_main(session)
        if t == "9":
            return reset_to_main(session)
        # Red flag detection
        from hooks import RED_FLAG_TERMS, normalize_text
        norm = normalize_text(t)
        if any(term in norm for term in RED_FLAG_TERMS):
            session["state"] = "RF_RED_FLAG"
            return ("😟 *Señal de alerta (prioridad)*  \nLamento lo que sientes. Puedo ayudarte con una *cita prioritaria*.  "
                    "Si los síntomas son intensos, acude a *emergencias* o llama al *911*.\n\n0️⃣ Atrás    1️⃣ Agendar prioritaria    2️⃣ Hablar con el Dr. Guzmán    9️⃣ Inicio")
        # Fallback: si el estado no es válido, forzar menú
        if session.get("state") not in {"MENU_PRINCIPAL", "RF_RED_FLAG"}:
            return reset_to_main(session)
        return None
    globals_cfg: Optional[Dict[str, Any]] = None

    def __post_init__(self) -> None:
        rules = (self.globals_cfg or {}).get("rules", {})
        self.slot_minutes = int(rules.get("slot_duration_minutes", SLOT_MINUTES_FALLBACK))
        self.gap_minutes = int(rules.get("gap_after_slot_minutes", GAP_MINUTES_FALLBACK))

    # ---------- Dispatcher ----------
    def call(self, name: str, *args, ctx: Optional[Dict[str, Any]] = None) -> Any:
        ctx = ctx or {}
        method_name = name.replace(".", "_")
        method = getattr(self, method_name, None)
        if not method:
            logger.warning("Hook %s is not implemented", name)
            return None
        try:
            return method(*args, ctx=ctx)
        except Exception:  # pragma: no cover
            logger.exception("Hook %s failed", name)
            return None

    # ---------- Core DB helpers ----------
    def _fetch_one(self, sql: str, params: Tuple[Any, ...]) -> Optional[Dict[str, Any]]:
        with _conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(sql, params)
                return cur.fetchone()

    def _fetch_all(self, sql: str, params: Tuple[Any, ...]) -> List[Dict[str, Any]]:
        with _conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(sql, params)
                return cur.fetchall()

    def _execute(self, sql: str, params: Tuple[Any, ...], *, fetch: Optional[str] = None) -> Any:
        with _conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(sql, params)
                result = None
                if fetch == "one":
                    result = cur.fetchone()
                elif fetch == "all":
                    result = cur.fetchall()
            conn.commit()
        return result

    # ---------- General helpers ----------
    def dates_today(self, *, ctx: Dict[str, Any]) -> str:
        return _now_local().date().strftime("%d-%m-%Y")

    def dates_tomorrow(self, *, ctx: Dict[str, Any]) -> str:
        return (_now_local().date() + timedelta(days=1)).strftime("%d-%m-%Y")

    def red_flag_detector(self, text: str, *, ctx: Dict[str, Any]) -> bool:

        normalized = _normalize(text)
        if not normalized:
            return False
        # Match any red flag term as substring, or if any word matches
        found = any(term in normalized for term in RED_FLAG_TERMS)
        # Extra: also match if any word in text matches a red flag word
        if not found:
            words = set(normalized.split())
            for term in RED_FLAG_TERMS:
                if any(word in term for word in words):
                    found = True
                    break
        if found:
            ctx.setdefault("flags", {})["red_flag"] = True
        return found

    def get_daypart_greeting(self, hour: int = None) -> str:
        """Return a greeting based on hour (default: now local)."""
        if hour is None:
            hour = _now_local().hour
        if 5 <= hour < 13:
            return "Buenos días"
        elif 13 <= hour < 20:
            return "Buenas tardes"
        else:
            return "Buenas noches"

    def is_greeting(self, text: str) -> bool:
        """Detect if text is a greeting."""
        norm = _normalize(text)
        return any(term in norm for term in GREETING_TERMS)

    def format_main_menu(self, options: list) -> str:
        """Format a main menu from a list of options (dicts with key/label)."""
        lines = []
        for opt in options:
            key = opt.get("key")
            label = opt.get("label")
            if key and label:
                lines.append(f"{key}. {label}")
        return "\n".join(lines)

    # ---------- Handoff ----------
    def handoff_to_human(self, platform: str, user_id: str, message: str, *, ctx: Dict[str, Any]) -> bool:
        payload = (platform or "").strip(), (user_id or "").strip(), (message or "").strip()
        self._execute(
            """
            INSERT INTO contact_requests (platform, user_key, raw_text)
            VALUES (%s, %s, %s)
            """,
            payload,
        )
        db_utils.log_handoff((user_id or "").strip(), message or ctx.get("last_text", ""), platform or "wa")
        ctx.setdefault("handoff", {})["requested"] = True
        logger.info("handoff requested platform=%s user=%s", platform, user_id)
        return True

    # ---------- Patients ----------
    def patient_lookup(self, dni: str, *, ctx: Dict[str, Any]) -> bool:
        dni = (dni or "").strip()
        ctx.setdefault("agenda", {})["dni"] = dni
        if not dni:
            ctx["agenda"]["patient"] = None
            return False
        row = self._fetch_one(
            """
            SELECT dni, full_name, birth_date, phone_ec, email, wa_user_id, tg_user_id
            FROM patients
            WHERE dni=%s
            """,
            (dni,),
        )
        if row:
            patient = self._patient_from_row(row)
            patient["summary"] = self._patient_summary(patient)
            ctx["agenda"]["patient"] = patient
            return True
        ctx["agenda"]["patient"] = None
        return False

    def patient_create_or_update(
        self,
        dni: str,
        full_name: str,
        birth_date: Optional[str],
        phone_ec: Optional[str],
        email: Optional[str],
        platform: Optional[str],
        user_id: Optional[str],
        *,
        ctx: Dict[str, Any],
    ) -> Dict[str, Any]:
        dni = (dni or "").strip()
        full_name = (full_name or "").strip()
        birth_date = (birth_date or "").strip() or None
        phone_ec = (phone_ec or "").strip() or None
        email = (email or "").strip() or None
        if email and email.lower() in {"ninguno", "ninguna", "no tengo", "omit", "0"}:
            email = None
        platform = (platform or "").strip().lower()
        user_id = (user_id or "").strip() or None
        wa_user_id = user_id if platform == "wa" else None
        tg_user_id = user_id if platform == "tg" else None

        row = self._execute(
            """
            INSERT INTO patients (dni, full_name, birth_date, phone_ec, email, wa_user_id, tg_user_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (dni) DO UPDATE SET
                full_name = EXCLUDED.full_name,
                birth_date = COALESCE(EXCLUDED.birth_date, patients.birth_date),
                phone_ec = COALESCE(EXCLUDED.phone_ec, patients.phone_ec),
                email = COALESCE(EXCLUDED.email, patients.email),
                wa_user_id = COALESCE(EXCLUDED.wa_user_id, patients.wa_user_id),
                tg_user_id = COALESCE(EXCLUDED.tg_user_id, patients.tg_user_id)
            RETURNING dni, full_name, birth_date, phone_ec, email, wa_user_id, tg_user_id, created_at
            """,
            (dni, full_name, birth_date, phone_ec, email, wa_user_id, tg_user_id),
            fetch="one",
        )
        patient = self._patient_from_row(row) if row else None
        if patient is not None:
            patient["summary"] = self._patient_summary(patient)
        ctx.setdefault("agenda", {})["patient"] = patient
        return patient or {}

    def _patient_from_row(self, row: Dict[str, Any]) -> Dict[str, Any]:
        if not row:
            return {}
        return {
            "dni": row.get("dni"),
            "full_name": row.get("full_name"),
            "birth_date": row.get("birth_date"),
            "phone_ec": row.get("phone_ec"),
            "email": row.get("email"),
            "wa_user_id": row.get("wa_user_id"),
            "tg_user_id": row.get("tg_user_id"),
        }

    def _patient_summary(self, patient: Dict[str, Any]) -> str:
        parts: List[str] = []
        name = patient.get("full_name") or "Sin nombre"
        parts.append(name)
        phone = patient.get("phone_ec")
        if phone:
            parts.append(f"Tel: {phone}")
        email = patient.get("email")
        if email:
            parts.append(f"Email: {email}")
        return " | ".join(parts)

    # ---------- Agenda: slots ----------
    def appointments_list_slots(self, site: str, date_str: str, *, ctx: Dict[str, Any]) -> List[Dict[str, str]]:
        site = (site or "").upper()
        ctx.setdefault("agenda", {})["site"] = site
        ctx["agenda"]["site_label"] = _site_label(site)
        try:
            target_date = _parse_date(date_str)
        except ValueError:
            ctx["agenda"]["slots"] = []
            return []
        if site != "GYE":
            ctx["agenda"]["slots"] = []
            return []
        existing = self._existing_local_slots(site, target_date)
        candidates = []
        now_local = _now_local()
        for candidate in _generate_candidates(target_date, self.slot_minutes, self.gap_minutes):
            if candidate.date() == now_local.date() and candidate <= now_local:
                continue
            if _slot_conflicts(candidate, existing, self.slot_minutes, self.gap_minutes):
                continue
            candidates.append(candidate)
        slots: List[Dict[str, str]] = []
        for idx, option in enumerate(candidates, start=1):
            slots.append(
                {
                    "key": str(idx),
                    "label": option.strftime("%d-%m-%Y %H:%M"),
                    "value": option.strftime("%d-%m-%Y %H:%M"),
                }
            )
        ctx["agenda"]["slots"] = slots
        return slots

    def agenda_store_slot(self, slot_label: str, *, ctx: Dict[str, Any]) -> bool:
        agenda = ctx.setdefault("agenda", {})
        try:
            local_dt = _parse_datetime_local(slot_label)
        except ValueError:
            agenda.pop("selected_slot", None)
            return False
        agenda["selected_slot"] = slot_label
        agenda["date"] = local_dt.strftime("%d-%m-%Y")
        agenda["time"] = local_dt.strftime("%H:%M")
        agenda["display"] = local_dt.strftime("%d-%m-%Y %H:%M")
        agenda["slot_dt"] = local_dt.isoformat()
        return True

    def _existing_local_slots(self, site: str, day: date) -> List[datetime]:
        start_utc, end_utc = _local_bounds(day)
        rows = self._fetch_all(
            """
            SELECT starts_at
            FROM appointments
            WHERE site=%s
              AND status IN ('PENDING','CONFIRMED')
              AND starts_at >= %s AND starts_at < %s
            """,
            (site, start_utc, end_utc),
        )
        out: List[datetime] = []
        for row in rows:
            dt = row.get("starts_at")
            if isinstance(dt, datetime):
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=TZ_UTC)
                out.append(dt.astimezone(TZ_LOCAL))
        return out

    # ---------- Bookings ----------
    def appointments_book_confirmed(self, reminder: str, *, ctx: Dict[str, Any]) -> Optional[int]:
        agenda = ctx.setdefault("agenda", {})
        patient = agenda.get("patient") or {}
        dni = patient.get("dni") or agenda.get("dni")
        slot_label = agenda.get("selected_slot")
        site = agenda.get("site", "GYE")
        if not (dni and slot_label and site):
            return None
        reminder_choice = (reminder or "wa").lower()
        if reminder_choice == "email" and not patient.get("email"):
            reminder_choice = "wa"
        try:
            local_dt = _parse_datetime_local(slot_label)
        except ValueError:
            return None
        if site.upper() == "GYE" and _slot_conflicts(local_dt, self._existing_local_slots(site, local_dt.date()), self.slot_minutes, self.gap_minutes):
            logger.info("Slot %s already taken at %s", slot_label, site)
            return None
        start_utc = local_dt.astimezone(TZ_UTC)
        row = self._execute(
            """
            INSERT INTO appointments (patient_dni, site, starts_at, status, reminder_channel)
            VALUES (%s, %s, %s, 'CONFIRMED', %s)
            RETURNING id
            """,
            (dni, site, start_utc, reminder_choice),
            fetch="one",
        )
        appointment_id = row.get("id") if row else None
        if appointment_id:
            agenda["reminder"] = reminder_choice
            agenda["display"] = agenda.get("display", slot_label)
            agenda["appointment"] = {
                "id": appointment_id,
                "site": site,
                "site_label": _site_label(site),
                "starts_at": slot_label,
                "reminder": reminder_choice,
            }
        return appointment_id
    def appointments_register_milagro(self, date_pref: str, shift: str, *, ctx: Dict[str, Any]) -> Optional[int]:
        agenda = ctx.setdefault("agenda", {})
        patient = agenda.get("patient") or {}
        dni = patient.get("dni") or agenda.get("dni")
        if not dni:
            return None
        try:
            target_date = _parse_date(date_pref)
        except ValueError:
            return None
        shift = (shift or "").lower()
        target_time = time(9, 0) if shift == "manana" else time(15, 0)
        local_dt = datetime.combine(target_date, target_time).replace(tzinfo=TZ_LOCAL)
        row = self._execute(
            """
            INSERT INTO appointments (patient_dni, site, starts_at, status, reminder_channel)
            VALUES (%s, 'MIL', %s, 'PENDING', %s)
            RETURNING id
            """,
            (dni, local_dt.astimezone(TZ_UTC), agenda.get("reminder", "wa")),
            fetch="one",
        )
        appointment_id = row.get("id") if row else None
        if appointment_id:
            agenda["reminder"] = agenda.get("reminder", "wa")
            agenda["display"] = local_dt.strftime("%d-%m-%Y %H:%M")
            agenda["appointment"] = {
                "id": appointment_id,
                "site": "MIL",
                "site_label": _site_label("MIL"),
                "starts_at": local_dt.strftime("%d-%m-%Y %H:%M"),
                "status": "PENDING",
            }
        return appointment_id
    def appointments_upcoming_by_dni(self, dni: str, *, ctx: Dict[str, Any]) -> bool:
        dni = (dni or "").strip()
        ctx.setdefault("agenda", {})["dni"] = dni
        if not dni:
            ctx.setdefault("appointments", {})["upcoming"] = []
            return False
        rows = self._fetch_all(
            """
            SELECT id, site, starts_at, status, reminder_channel
            FROM appointments
            WHERE patient_dni=%s AND status IN ('PENDING','CONFIRMED')
            ORDER BY starts_at ASC
            LIMIT 5
            """,
            (dni,),
        )
        upcoming: List[Dict[str, Any]] = []
        for row in rows:
            starts_at = row.get("starts_at")
            if isinstance(starts_at, datetime):
                if starts_at.tzinfo is None:
                    starts_at = starts_at.replace(tzinfo=TZ_UTC)
                local_dt = starts_at.astimezone(TZ_LOCAL)
                upcoming.append(
                    {
                        "id": row.get("id"),
                        "site": row.get("site"),
                        "site_label": _site_label(row.get("site", "")),
                        "local_label": local_dt.strftime("%d-%m-%Y %H:%M"),
                        "date": local_dt.strftime("%d-%m-%Y"),
                        "time": local_dt.strftime("%H:%M"),
                        "status": row.get("status"),
                        "reminder": row.get("reminder_channel"),
                    }
                )
        ctx.setdefault("appointments", {})["upcoming"] = upcoming
        if upcoming:
            ctx["appointments"]["target"] = upcoming[0]
            agenda = ctx.setdefault("agenda", {})
            agenda["site"] = upcoming[0]["site"]
            agenda["site_label"] = upcoming[0]["site_label"]
            agenda["date"] = upcoming[0]["date"]
            agenda["time"] = upcoming[0]["time"]
            agenda["selected_slot"] = upcoming[0]["local_label"]
        return bool(upcoming)

    def appointments_reschedule(self, appointment_id: int, new_slot: str, *, ctx: Dict[str, Any]) -> bool:
        if not appointment_id:
            return False
        try:
            local_dt = _parse_datetime_local(new_slot)
        except ValueError:
            return False
        site = (ctx.get("agenda", {}).get("site") or "GYE").upper()
        if site == "GYE" and _slot_conflicts(local_dt, self._existing_local_slots(site, local_dt.date()), self.slot_minutes, self.gap_minutes):
            logger.info("Conflict while rescheduling appointment %s", appointment_id)
            return False
        updated = self._execute(
            """
            UPDATE appointments
            SET starts_at=%s, status='CONFIRMED'
            WHERE id=%s
            RETURNING id
            """,
            (local_dt.astimezone(TZ_UTC), appointment_id),
            fetch="one",
        )
        if updated:
            ctx.setdefault("appointments", {}).setdefault("target", {})["local_label"] = local_dt.strftime("%d-%m-%Y %H:%M")
            agenda = ctx.setdefault("agenda", {})
            agenda["date"] = local_dt.strftime("%d-%m-%Y")
            agenda["time"] = local_dt.strftime("%H:%M")
            agenda["selected_slot"] = local_dt.strftime("%d-%m-%Y %H:%M")
            agenda["display"] = local_dt.strftime("%d-%m-%Y %H:%M")
        return bool(updated)

    def appointments_cancel(self, appointment_id: int, *, ctx: Dict[str, Any]) -> bool:
        if not appointment_id:
            return False
        self._execute(
            "UPDATE appointments SET status='CANCELLED' WHERE id=%s",
            (appointment_id,),
        )
        ctx.setdefault("appointments", {}).setdefault("target", {})["status"] = "CANCELLED"
        return True

    def appointments_set_reminder(self, appointment_id: int, reminder: str, *, ctx: Dict[str, Any]) -> bool:
        if not appointment_id:
            return False
        self._execute(
            "UPDATE appointments SET reminder_channel=%s WHERE id=%s",
            (reminder, appointment_id),
        )
        ctx.setdefault("appointments", {}).setdefault("target", {})["reminder"] = reminder
        return True
















