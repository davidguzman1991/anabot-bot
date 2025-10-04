# db_utils.py
# Utilidades de acceso a PostgreSQL para AnaBot
# ---------------------------------------------
# - Conexión segura usando DATABASE_URL (Railway)
# - Helpers: fetchone, fetchall, execute
# - Pacientes: get/create/update
# - Citas: create/get-active/update-status/reschedule/cancel
# - Health check

from __future__ import annotations

import os
import logging
from typing import Any, Dict, List, Optional, Tuple, Union

import psycopg2
import psycopg2.extras

# ------------------------------------------------------------------------------
# Configuración de logging
# ------------------------------------------------------------------------------
logger = logging.getLogger("anabot.db")
if not logger.handlers:
    _h = logging.StreamHandler()
    _fmt = logging.Formatter("[%(asctime)s] %(levelname)s %(name)s: %(message)s")
    _h.setFormatter(_fmt)
    logger.addHandler(_h)
logger.setLevel(logging.INFO)

# ------------------------------------------------------------------------------
# Conexión
# ------------------------------------------------------------------------------

def _dsn_from_env() -> str:
    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        raise RuntimeError("DATABASE_URL no está definido en el entorno.")
    return dsn

def get_conn():
    """
    Devuelve una conexión nueva por llamada.
    Railway expone DATABASE_URL estilo Postgres. No usamos pool para mantener simplicidad.
    """
    dsn = _dsn_from_env()
    # En Railway el sslmode suele venir en el DSN; si no, puedes forzarlo:
    # dsn += "?sslmode=require"
    conn = psycopg2.connect(dsn)
    conn.autocommit = False
    return conn

# ------------------------------------------------------------------------------
# Helpers de consulta
# ------------------------------------------------------------------------------

def fetchone(query: str, params: Union[Tuple, List, None] = None) -> Optional[Dict[str, Any]]:
    """
    Ejecuta una consulta y devuelve un dict o None.
    """
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            logger.debug("SQL fetchone: %s | %s", query, params)
            cur.execute(query, params)
            row = cur.fetchone()
        conn.commit()
    return dict(row) if row else None

def fetchall(query: str, params: Union[Tuple, List, None] = None) -> List[Dict[str, Any]]:
    """
    Ejecuta una consulta y devuelve lista de dicts.
    """
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            logger.debug("SQL fetchall: %s | %s", query, params)
            cur.execute(query, params)
            rows = cur.fetchall()
        conn.commit()
    return [dict(r) for r in rows]

def execute(query: str, params: Union[Tuple, List, None] = None) -> int:
    """
    Ejecuta INSERT/UPDATE/DELETE. Devuelve filas afectadas.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            logger.debug("SQL execute: %s | %s", query, params)
            cur.execute(query, params)
            affected = cur.rowcount
        conn.commit()
    return affected

# ------------------------------------------------------------------------------
# Health check
# ------------------------------------------------------------------------------

def db_health() -> Dict[str, Any]:
    """
    Comprueba salud de BD con una consulta simple.
    """
    try:
        row = fetchone("SELECT NOW() AT TIME ZONE 'UTC' as now_utc;", None)
        return {"ok": True, "now_utc": row["now_utc"] if row else None}
    except Exception as e:
        logger.exception("db_health error")
        return {"ok": False, "error": str(e)}

# ------------------------------------------------------------------------------
# Pacientes
# Tabla: public.patients
# Campos esperados:
#  - dni (text, PK/UNIQUE)
#  - full_name (text)
#  - birth_date (date)
#  - phone_ec (text)
#  - email (text)
#  - wa_user_id (text)  [opcional]
#  - tg_user_id (text)  [opcional]
#  - created_at (timestamptz)
# ------------------------------------------------------------------------------

def get_patient_by_dni(dni: str) -> Optional[Dict[str, Any]]:
    """
    Busca un paciente por DNI/pasaporte exacto.
    """
    q = "SELECT * FROM public.patients WHERE dni = %s;"
    return fetchone(q, (dni,))

def create_patient(
    *,
    dni: str,
    full_name: str,
    birth_date: Optional[str],  # 'YYYY-MM-DD' o None
    phone_ec: Optional[str],
    email: Optional[str],
    wa_user_id: Optional[str] = None,
    tg_user_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Crea un paciente. Devuelve el registro insertado (dni).
    Nota: birth_date debe venir ya normalizado a 'YYYY-MM-DD' (o None).
    """
    q = """
        INSERT INTO public.patients
            (dni, full_name, birth_date, phone_ec, email, wa_user_id, tg_user_id, created_at)
        VALUES
            (%s,  %s,        %s,         %s,       %s,    %s,         %s,        NOW())
        RETURNING dni;
    """
    row = fetchone(q, (dni, full_name, birth_date, phone_ec, email, wa_user_id, tg_user_id))
    return row if row else {"dni": dni}

def update_patient_contacts(
    *,
    dni: str,
    phone_ec: Optional[str] = None,
    email: Optional[str] = None,
) -> int:
    """
    Actualiza datos de contacto si vienen no-nulos.
    """
    sets = []
    params: List[Any] = []
    if phone_ec is not None:
        sets.append("phone_ec = %s")
        params.append(phone_ec)
    if email is not None:
        sets.append("email = %s")
        params.append(email)

    if not sets:
        return 0

    params.append(dni)
    q = f"UPDATE public.patients SET {', '.join(sets)} WHERE dni = %s;"
    return execute(q, params)

# ------------------------------------------------------------------------------
# Citas
# Tabla: public.appointments
# Campos esperados:
#  - id (bigint pk)
#  - patient_dni (text)
#  - site (text)               -> sede/ciudad
#  - starts_at (timestamptz)   -> fecha/hora
#  - status (text)             -> p.ej. 'programada', 'cancelada', 'reagendada'
#  - reminder_channel (text)   -> opcional: 'whatsapp', 'email', etc.
#  - created_at (timestamptz)
# ------------------------------------------------------------------------------

def save_appointment(
    *,
    patient_dni: str,
    site: str,
    starts_at: str,           # ISO 'YYYY-MM-DD HH:MM:SS+00' o 'YYYY-MM-DD HH:MM'
    reminder_channel: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Crea una nueva cita 'programada'. Devuelve {id}.
    """
    if reminder_channel:
        q = """
            INSERT INTO public.appointments
                (patient_dni, site, starts_at, status, reminder_channel, created_at)
            VALUES
                (%s,          %s,   %s,        'programada', %s,            NOW())
            RETURNING id;
        """
        params = (patient_dni, site, starts_at, reminder_channel)
    else:
        q = """
            INSERT INTO public.appointments
                (patient_dni, site, starts_at, status, created_at)
            VALUES
                (%s,          %s,   %s,        'programada', NOW())
            RETURNING id;
        """
        params = (patient_dni, site, starts_at)

    row = fetchone(q, params)
    return row if row else {}

def get_active_appointment_by_dni(dni: str) -> Optional[Dict[str, Any]]:
    """
    Obtiene la última cita 'programada' de un paciente.
    """
    q = """
        SELECT id, patient_dni, site, starts_at, status, reminder_channel, created_at
        FROM public.appointments
        WHERE patient_dni = %s AND status = 'programada'
        ORDER BY starts_at DESC
        LIMIT 1;
    """
    return fetchone(q, (dni,))

def update_appointment_status_by_dni(dni: str, new_status: str) -> Optional[Dict[str, Any]]:
    """
    Actualiza la cita 'programada' de un paciente a un nuevo estado
    (p.ej., 'cancelada' o 'reagendada'). Devuelve {id} de la cita afectada.
    """
    q = """
        UPDATE public.appointments
        SET status = %s
        WHERE patient_dni = %s AND status = 'programada'
        RETURNING id;
    """
    return fetchone(q, (new_status, dni))

def reschedule_appointment(
    *,
    dni: str,
    new_starts_at: str,
    site: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """
    Reagenda la cita 'programada' (cambia starts_at y opcionalmente site).
    Devuelve {id} de la cita modificada.
    """
    if site:
        q = """
            UPDATE public.appointments
            SET starts_at = %s, site = %s, status = 'programada'
            WHERE patient_dni = %s AND status = 'programada'
            RETURNING id;
        """
        params = (new_starts_at, site, dni)
    else:
        q = """
            UPDATE public.appointments
            SET starts_at = %s, status = 'programada'
            WHERE patient_dni = %s AND status = 'programada'
            RETURNING id;
        """
        params = (new_starts_at, dni)

    return fetchone(q, params)

def cancel_appointment(dni: str) -> Optional[Dict[str, Any]]:
    """
    Cancela la cita activa.
    """
    return update_appointment_status_by_dni(dni, "cancelada")

# ------------------------------------------------------------------------------
# Utilidades varias (opcional)
# ------------------------------------------------------------------------------

def get_last_messages(limit: int = 50) -> List[Dict[str, Any]]:
    """
    Ejemplo de utilitario de auditoría si tuvieras tabla messages (opcional).
    No falla si la tabla no existe; útil para diagnósticos.
    """
    try:
        return fetchall("SELECT * FROM public.messages ORDER BY ts DESC LIMIT %s;", (limit,))
    except Exception:
        return []
