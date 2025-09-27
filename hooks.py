# hooks.py
# Lógica de negocio para el bot "Ana" con persistencia en PostgreSQL.
# Python 3.10+, requiere psycopg2-binary. Usa TZ America/Guayaquil y guarda en BD en UTC.

from __future__ import annotations
import os
import logging
import unicodedata
from dataclasses import dataclass
from typing import Any, Dict, List, Tuple, Optional
from datetime import datetime, date, time, timedelta
from zoneinfo import ZoneInfo

import psycopg2
from psycopg2.extras import RealDictCursor

# ---------------------------
# Configuración global
# ---------------------------
TZ = ZoneInfo("America/Guayaquil")
DATABASE_URL = os.getenv("DATABASE_URL")

logger = logging.getLogger("hooks")
if not logger.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s"))
    logger.addHandler(_h)
logger.setLevel(logging.INFO)

# Red flags (texto normalizado sin acentos)
RED_FLAGS = [
    "dolor en el pecho", "dificultad para respirar", "disnea", "ahogo",
    "fiebre alta", "desmayo", "desvanecimiento", "confusion",
    "vision borrosa", "hipoglucemia", "sudor frio", "dolor toracico"
]

# Jornadas Guayaquil: L-V 09-12 y 16-20; Sáb 09-16; Dom sin atención
GYE_WINDOWS: Dict[int, List[Tuple[time, time]]] = {
    0: [(time(9, 0), time(12, 0)), (time(16, 0), time(20, 0))],
    1: [(time(9, 0), time(12, 0)), (time(16, 0), time(20, 0))],
    2: [(time(9, 0), time(12, 0)), (time(16, 0), time(20, 0))],
    3: [(time(9, 0), time(12, 0)), (time(16, 0), time(20, 0))],
    4: [(time(9, 0), time(12, 0)), (time(16, 0), time(20, 0))],
    5: [(time(9, 0), time(16, 0))],
    6: []
}

# ---------------------------
# Utilidades
# ---------------------------
def _now() -> datetime:
    return datetime.now(tz=TZ)

def _normalize_text(s: str) -> str:
    s = s or ""
    s = s.lower()
    s = "".join(ch for ch in unicodedata.normalize("NFD", s) if unicodedata.category(ch) != "Mn")
    return s

def _round_to_15(dt_obj: datetime) -> datetime:
    m = (dt_obj.minute // 15) * 15
    return dt_obj.replace(minute=m, second=0, microsecond=0)

def _overlaps(a_start: datetime, a_end: datetime, b_start: datetime, b_end: datetime) -> bool:
    return not (a_end <= b_start or b_end <= a_start)

def _expand_by_gap(start: datetime, end: datetime, gap_min: int) -> Tuple[datetime, datetime]:
    gap = timedelta(minutes=gap_min)
    return start - gap, end + gap

def _parse_dmy(fecha_dmy: str) -> date:
    return datetime.strptime(fecha_dmy, "%d-%m-%Y").date()

def _parse_dmy_hm(fecha_dmy: str, hora_hm: str) -> datetime:
    return datetime.strptime(f"{fecha_dmy} {hora_hm}", "%d-%m-%Y %H:%M").replace(tzinfo=TZ)

def _to_utc(dt_local: datetime) -> datetime:
    if dt_local.tzinfo is None:
        dt_local = dt_local.replace(tzinfo=TZ)
    return dt_local.astimezone(ZoneInfo("UTC"))

def _from_utc_to_local(dt_utc: datetime) -> datetime:
    if dt_utc.tzinfo is None:
        dt_utc = dt_utc.replace(tzinfo=ZoneInfo("UTC"))
    return dt_utc.astimezone(TZ)

# ---------------------------
# Acceso a base de datos
# ---------------------------
def db_conn():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL no configurada")
    # Railway/Neon suelen requerir sslmode=require ya en la URL
    return psycopg2.connect(DATABASE_URL)

def db_query(sql: str, params: Tuple = (), fetch: str = "all"):
    with db_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, params)
            if fetch == "one":
                return cur.fetchone()
            if fetch == "all":
                return cur.fetchall()
            return None

# ---------------------------
# Slots / disponibilidad
# ---------------------------
def get_existing_appointments_day(sede: str, day: date) -> List[Tuple[datetime, datetime]]:
    """
    Devuelve lista de tuplas (inicio_local, fin_local) para la sede y fecha dadas.
    """
    rows = db_query(
        """
        SELECT (inicio AT TIME ZONE 'UTC') AS s_utc,
               (fin    AT TIME ZONE 'UTC') AS e_utc
        FROM appointments
        WHERE sede=%s
          AND date((inicio AT TIME ZONE 'UTC') AT TIME ZONE %s) = %s
          AND estado IN ('confirmada','pendiente')
        """,
        (sede, str(TZ), day),
        fetch="all"
    )
    out: List[Tuple[datetime, datetime]] = []
    for r in rows:
        s_local = _from_utc_to_local(r["s_utc"])
        e_local = _from_utc_to_local(r["e_utc"])
        out.append((s_local, e_local))
    return out

def free_slots_for_day_gye(d: date, dur_min: int, gap_min: int, max_slots: int, min_lead_hours: int = 2) -> List[Dict[str, str]]:
    windows = GYE_WINDOWS.get(d.weekday(), [])
    existing = get_existing_appointments_day("Guayaquil", d)
    out: List[Dict[str, str]] = []
    now = _now()

    for w_start, w_end in windows:
        cur = _round_to_15(datetime.combine(d, w_start).replace(tzinfo=TZ))
        last = datetime.combine(d, w_end).replace(tzinfo=TZ) - timedelta(minutes=dur_min)
        while cur <= last and len(out) < max_slots:
            cand_start = cur
            cand_end = cur + timedelta(minutes=dur_min)

            # margen mínimo 2h
            if cand_start < now + timedelta(hours=min_lead_hours):
                cur += timedelta(minutes=15)
                continue

            # evitar choques (incluye gap)
            gs, ge = _expand_by_gap(cand_start, cand_end, gap_min)
            conflict = False
            for (s, e) in existing:
                if _overlaps(gs, ge, s, e):
                    conflict = True
                    break
            if conflict:
                cur += timedelta(minutes=15)
                continue

            hm = cand_start.strftime("%H:%M")
            idx = len(out) + 1
            out.append({"key": str(idx), "label": hm, "value": hm})
            cur += timedelta(minutes=15)

        if len(out) >= max_slots:
            break
    return out

# ---------------------------
# Proxy DB (para scheduler/main)
# ---------------------------
class PatientsMap:
    def get(self, cedula: str, default=None):
        row = db_query("SELECT cedula, nombres, apellidos, telefono, email FROM patients WHERE cedula=%s",
                       (cedula,), fetch="one")
        if not row:
            return default
        return dict(row)

class AppointmentsIterable:
    def __iter__(self):
        rows = db_query(
            """
            SELECT id, cedula, sede,
                   (inicio AT TIME ZONE 'UTC') AS inicio_utc,
                   (fin    AT TIME ZONE 'UTC') AS fin_utc,
                   estado
            FROM appointments
            WHERE estado IN ('confirmada','pendiente')
              AND inicio >= now() - interval '1 hour'
              AND inicio <= now() + interval '7 day'
            ORDER BY inicio ASC
            """,
            fetch="all"
        )
        for r in rows:
            start = _from_utc_to_local(r["inicio_utc"])
            end   = _from_utc_to_local(r["fin_utc"])
            yield {"id": r["id"], "cedula": r["cedula"], "sede": r["sede"], "inicio": start, "fin": end, "estado": r["estado"]}

DB: Dict[str, Any] = {
    "patients": PatientsMap(),
    "appointments": AppointmentsIterable(),
}

# ---------------------------
# Clase principal de hooks
# ---------------------------
@dataclass
class Hooks:
    globals_cfg: Dict[str, Any]

    def __post_init__(self):
        self.rules = self.globals_cfg.get("rules", {}) if self.globals_cfg else {}
        self.dur = int(self.rules.get("slot_duration_minutes", 45))
        self.gap = int(self.rules.get("gap_after_slot_minutes", 15))

    # ------------ Riesgo / red flags ------------
    def risk_red_flag_scan(self, ctx: Dict[str, Any], *args) -> bool:
        text = _normalize_text(ctx.get("last_text", ""))
        found = any(term in text for term in RED_FLAGS)
        if found:
            logger.info("[RF] Red flag detectada en entrada del usuario.")
        return found

    # ------------ Fechas helpers ------------
    def dates_today(self, ctx: Dict[str, Any]) -> str:
        return _now().date().strftime("%d-%m-%Y")

    def dates_tomorrow(self, ctx: Dict[str, Any]) -> str:
        return (_now().date() + timedelta(days=1)).strftime("%d-%m-%Y")

    def dates_parse_and_save(self, fecha_str: str, save_key: str, ctx: Dict[str, Any]) -> bool:
        # Valida formato
        try:
            _ = _parse_dmy(fecha_str)
        except Exception:
            return False
        # Guarda en ctx con notación a.b.c
        parts = save_key.split(".")
        cur = ctx
        for i, p in enumerate(parts):
            if i == len(parts) - 1:
                cur[p] = fecha_str
            else:
                cur = cur.setdefault(p, {})
        return True

    # ------------ Paciente ------------
    def db_find_patient_by_id(self, cedula: str, ctx: Dict[str, Any]):
        return db_query("SELECT * FROM patients WHERE cedula=%s", (cedula,), fetch="one")

    def db_upsert_patient_min(self, cedula: str, nombre_apellidos: str, ctx: Dict[str, Any]):
        parts = (nombre_apellidos or "").strip().split()
        if len(parts) >= 2:
            apellidos = parts[-1]
            nombres = " ".join(parts[:-1])
        else:
            nombres = parts[0] if parts else ""
            apellidos = ""
        db_query("""
            INSERT INTO patients (cedula, nombres, apellidos)
            VALUES (%s,%s,%s)
            ON CONFLICT (cedula) DO UPDATE
            SET nombres=EXCLUDED.nombres, apellidos=EXCLUDED.apellidos
        """, (cedula, nombres, apellidos), fetch=None)
        ctx["paciente"] = {"cedula": cedula, "nombres": nombres, "apellidos": apellidos}
        return ctx["paciente"]

    def db_update_patient_field(self, campo: str, valor: str, ctx: Dict[str, Any]) -> bool:
        allowed = {"telefono", "email", "fnac", "nombres", "apellidos"}
        if campo not in allowed:
            return False
        pac = ctx.get("paciente") or {}
        ced = pac.get("cedula")
        if not ced:
            return False
        if campo == "fnac":
            try:
                valor_sql = datetime.strptime(valor, "%d-%m-%Y").date()
            except Exception:
                return False
        else:
            valor_sql = valor
        db_query(f"UPDATE patients SET {campo}=%s WHERE cedula=%s", (valor_sql, ced), fetch=None)
        ctx.setdefault("paciente", {})[campo] = valor
        return True

    # ------------ Citas: sugerir ------------
    def appointments_suggest_slots(self, sede: str, fecha: str, rules: Dict[str, Any], dur: int, gap: int, ctx: Dict[str, Any]):
        d = _parse_dmy(fecha)
        if (sede or "").lower().startswith("guaya"):
            slots = free_slots_for_day_gye(d, dur_min=dur, gap_min=gap, max_slots=3)
        else:
            # Milagro es confirmación diferida por ahora: sin slots
            slots = []
        ctx.setdefault("cita", {})["slots"] = slots
        return slots

    def appointments_suggest_slots_for_existing(self, sede: str, fecha: str, dur: int, gap: int, ctx: Dict[str, Any]):
        d = _parse_dmy(fecha)
        if (sede or "").lower().startswith("guaya"):
            slots = free_slots_for_day_gye(d, dur_min=dur, gap_min=gap, max_slots=3)
        else:
            slots = []
        ctx.setdefault("cita", {})["slots"] = slots
        return slots

    # ------------ Citas: reservar / prioritaria ------------
    def _has_conflict(self, sede: str, start: datetime, end: datetime, gap: int) -> bool:
        rows = db_query(
            """
            SELECT (inicio AT TIME ZONE 'UTC') AS s_utc,
                   (fin    AT TIME ZONE 'UTC') AS e_utc
            FROM appointments
            WHERE sede=%s
              AND date((inicio AT TIME ZONE 'UTC') AT TIME ZONE %s) = %s
              AND estado IN ('confirmada','pendiente')
            """, (sede, str(TZ), start.date()), fetch="all"
        )
        gs, ge = _expand_by_gap(start, end, gap)
        for r in rows:
            s_l = _from_utc_to_local(r["s_utc"])
            e_l = _from_utc_to_local(r["e_utc"])
            if _overlaps(gs, ge, s_l, e_l):
                return True
        return False

    def appointments_reserve_if_free(self, sede: str, fecha: str, hora: str, dur: int, gap: int, ctx: Dict[str, Any]):
        cedula = (ctx.get("paciente") or {}).get("cedula")
        if not cedula:
            return None
        start = _parse_dmy_hm(fecha, hora)
        end = start + timedelta(minutes=dur)
        if self._has_conflict(sede, start, end, gap):
            logger.info("[APPT] Conflicto al reservar.")
            return None
        row = db_query(
            "INSERT INTO appointments (cedula, sede, inicio, fin, estado) VALUES (%s,%s,%s,%s,'confirmada') RETURNING id",
            (cedula, sede, _to_utc(start), _to_utc(end)),
            fetch="one"
        )
        apt_id = row["id"]
        ctx["appointment"] = {"id": apt_id, "sede": sede, "inicio": start.isoformat()}
        logger.info(f"[APPT] Reservada cita id={apt_id} {start.isoformat()} {sede} para {cedula}")
        return apt_id

    def appointments_next_slots(self, sede: str, count: int, within_hours: int, ctx: Dict[str, Any]):
        out: List[Dict[str, str]] = []
        now = _now()
        deadline = now + timedelta(hours=within_hours)
        cursor = now.date()
        while cursor <= deadline.date() and len(out) < count:
            day_slots = free_slots_for_day_gye(cursor, dur_min=self.dur, gap_min=self.gap, max_slots=count - len(out))
            for s in day_slots:
                hm = s["value"]
                start_dt = datetime.combine(cursor, datetime.strptime(hm, "%H:%M").time()).replace(tzinfo=TZ)
                if now <= start_dt <= deadline:
                    idx = len(out) + 1
                    out.append({"key": str(idx), "label": start_dt.strftime("%d-%m-%Y %H:%M"), "value": start_dt.strftime("%d-%m-%Y %H:%M")})
            cursor += timedelta(days=1)
        ctx.setdefault("cita", {})["slots"] = out
        return out

    def appointments_reserve_priority(self, sede: str, fechor: str, dur: int, gap: int, ctx: Dict[str, Any]):
        cedula = (ctx.get("paciente") or {}).get("cedula")
        if not cedula:
            return None
        # fechor = "DD-MM-YYYY HH:MM"
        start = datetime.strptime(fechor, "%d-%m-%Y %H:%M").replace(tzinfo=TZ)
        end = start + timedelta(minutes=dur)
        if self._has_conflict(sede, start, end, gap):
            logger.info("[APPT] Conflicto en reserva prioritaria.")
            return None
        row = db_query(
            "INSERT INTO appointments (cedula, sede, inicio, fin, estado) VALUES (%s,%s,%s,%s,'confirmada') RETURNING id",
            (cedula, sede, _to_utc(start), _to_utc(end)),
            fetch="one"
        )
        ctx["appointment"] = {"id": row["id"], "sede": sede, "inicio": start.isoformat()}
        logger.info(f"[APPT] Prioritaria id={row['id']} {start.isoformat()} {sede} para {cedula}")
        return row["id"]

    # ------------ Citas: listar / reagendar / cancelar ------------
    def appointments_find_upcoming_by_id(self, cedula: str, ctx: Dict[str, Any]):
        rows = db_query(
            """
            SELECT id, sede,
                   (inicio AT TIME ZONE 'UTC') AS inicio_utc,
                   (fin    AT TIME ZONE 'UTC') AS fin_utc,
                   estado
            FROM appointments
            WHERE cedula=%s
              AND estado IN ('confirmada','pendiente')
              AND inicio >= now()
            ORDER BY inicio ASC
            """, (cedula,), fetch="all"
        )
        results: List[Dict[str, Any]] = []
        for r in rows:
            start = _from_utc_to_local(r["inicio_utc"])
            label = f"{start.strftime('%d-%m-%Y %H:%M')} · {r['sede']}"
            results.append({"key": str(r["id"]), "label": label, "value": {"id": r["id"], "sede": r["sede"]}})
        ctx.setdefault("citas", {})["upcoming"] = results
        return results

    def ui_render_appointments_list(self, citas: list, ctx: Dict[str, Any]) -> bool:
        return bool(citas)

    def appointments_reschedule(self, apt_id: int, fecha: str, hora: str, dur: int, gap: int, ctx: Dict[str, Any]) -> bool:
        new_start = _parse_dmy_hm(fecha, hora)
        new_end = new_start + timedelta(minutes=dur)
        sede = (ctx.get("cita", {}).get("target") or {}).get("sede", "Guayaquil")
        if self._has_conflict(sede, new_start, new_end, gap):
            logger.info(f"[APPT] Conflicto al reagendar id={apt_id}")
            return False
        db_query("UPDATE appointments SET inicio=%s, fin=%s WHERE id=%s",
                 (_to_utc(new_start), _to_utc(new_end), int(apt_id)), fetch=None)
        logger.info(f"[APPT] Reagendada id={apt_id} a {new_start.isoformat()} {sede}")
        return True

    def appointments_cancel(self, apt_id: int, motivo: str, ctx: Dict[str, Any]) -> bool:
        db_query("UPDATE appointments SET estado='cancelada', motivo_cancel=%s WHERE id=%s", (motivo, int(apt_id)), fetch=None)
        logger.info(f"[APPT] Cancelada id={apt_id} motivo='{motivo or ''}'")
        return True

    # ------------ Recordatorios (registro lógico) ------------
    def reminders_set(self, canal: str, appointment_id: int, ctx: Dict[str, Any]) -> bool:
        ctx.setdefault("reminders", []).append({"canal": canal, "appointment_id": appointment_id})
        logger.info(f"[REM] Preferencia canal={canal} para appointment_id={appointment_id}")
        return True

    # ------------ Handoff / contacto con el Dr. ------------
    def handoff_to_human(self, ctx: Dict[str, Any]) -> bool:
        ctx["handoff"] = True
        logger.info("[HANDOFF] Transferencia a humano solicitada.")
        return True

    def handoff_notify_doctor(self, nombre: str, telefono: str, ctx: Dict[str, Any]) -> bool:
        # Tabla opcional para leads de contacto
        try:
            db_query("""
              CREATE TABLE IF NOT EXISTS contact_requests (
                id SERIAL PRIMARY KEY,
                nombre TEXT NOT NULL,
                telefono TEXT NOT NULL,
                created_at TIMESTAMPTZ DEFAULT now()
              )
            """, fetch=None)
            db_query("INSERT INTO contact_requests (nombre, telefono) VALUES (%s,%s)", (nombre, telefono), fetch=None)
        except Exception as e:
            logger.warning(f"[HANDOFF] No se pudo registrar contact_request: {e}")
        logger.info(f"[HANDOFF] Paciente pide hablar con el Dr.: {nombre} / {telefono}")
        ctx.setdefault("handoff", {})["doctor"] = {"nombre": nombre, "telefono": telefono}
        return True


# ---------------------------
# (Opcional) Pruebas manuales
# ---------------------------
# if __name__ == "__main__":
#     hooks = Hooks(globals_cfg={"rules": {"slot_duration_minutes": 45, "gap_after_slot_minutes": 15}})
#     ctx = {}
#     print("Hoy:", hooks.dates_today(ctx))
#     print("Mañana:", hooks.dates_tomorrow(ctx))
#     # Sugerir slots:
#     ctx["cita"] = {"sede": "Guayaquil"}
#     slots = hooks.appointments_suggest_slots("Guayaquil", hooks.dates_tomorrow(ctx), {}, 45, 15, ctx)
#     print("Slots:", slots)
