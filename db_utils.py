# db_utils.py — versión estable
import os
import time
import logging
import psycopg2
from psycopg2.extras import RealDictCursor

logger = logging.getLogger("db")
logger.setLevel(logging.INFO)

DATABASE_URL = os.getenv("DATABASE_URL")

def get_conn():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL no configurada")
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

def wait_for_db(max_attempts: int = 10, delay: float = 1.5) -> bool:
    """Intenta conectar varias veces antes de rendirse (para containers que arrancan lento)."""
    for i in range(1, max_attempts + 1):
        try:
            with get_conn() as conn, conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
            logger.info("[DB] saludable en intento %s/%s", i, max_attempts)
            return True
        except Exception as e:
            logger.warning("[DB] intento %s/%s falló: %s", i, max_attempts, e)
            time.sleep(delay)
    return False

def db_health() -> bool:
    """True si la BD responde; False si no."""
    try:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT 1")
            cur.fetchone()
        return True
    except Exception as e:
        logger.error("[DB] healthcheck falló: %s", e)
        return False
import time

def wait_for_db(max_attempts: int = 10, delay: float = 1.5):
    for i in range(1, max_attempts + 1):
        try:
            with get_conn() as conn, conn.cursor() as cur:
                cur.execute("SELECT 1")
                _ = cur.fetchone()
            return True
        except Exception as e:
            print(f"[DB] intento {i}/{max_attempts} falló: {e}")
            time.sleep(delay)
    return False

import os
import psycopg2
from psycopg2.extras import RealDictCursor

DATABASE_URL = os.getenv("DATABASE_URL")

def get_conn():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL no configurada")
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

def db_health():
    try:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT 1")
            _ = cur.fetchone()
        return True
    except Exception:
        return False

def fetchone(query, params=None):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(query, params or ())
        return cur.fetchone()

def fetchall(query, params=None):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(query, params or ())
        return cur.fetchall()

def execute(query, params=None):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(query, params or ())
        conn.commit()
        return cur.rowcount


def log_handoff(user_id: str, last_text: str, platform: str = "wa"):
    try:
        conn = _conn()
        if not conn:
            return False
        with conn:
            with conn.cursor() as cur:
                sql = "INSERT INTO public.conversation_logs (user_id, message, platform, handoff, status) VALUES (%s, %s, %s, %s, %s)"
                logger.info("INSERT public.conversation_logs columns=[user_id, message, platform, handoff, status]")
                cur.execute(sql, (user_id, last_text or "", platform, True, "pendiente"))
            conn.commit()
        return True
    except Exception as e:
        logger.exception("db error in log_handoff")
        try:
            if conn:
                conn.rollback()
        except Exception:
            pass
        return False


def save_appointment(user_id: str, ts: str, status: str = "pendiente"):
    try:
        conn = _conn()
        if not conn:
            return False
        with conn:
            with conn.cursor() as cur:
                sql = "INSERT INTO public.appointments (user_id, appointment_date, status) VALUES (%s, %s, %s)"
                logger.info("INSERT public.appointments columns=[user_id, appointment_date, status]")
                cur.execute(sql, (user_id, ts, status))
            conn.commit()
        return True
    except Exception as e:
        logger.exception("db error in save_appointment")
        try:
            if conn:
                conn.rollback()
        except Exception:
            pass
        return False

# --- DB Health Check ---
def db_health():
    try:
        conn = _conn()
        if not conn:
            return {"db": "unavailable"}
        with conn:
            with conn.cursor() as cur:
                cur.execute("SELECT current_database(), current_schema()")
                dbname, schema = cur.fetchone()
                cur.execute("SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name='conversation_logs'")
                columns = [row[0] for row in cur.fetchall()]
        return {"db": "ok", "database": dbname, "schema": schema, "columns": columns}
    except Exception as e:
        logger.exception("db error in db_health")
        return {"db": "error", "error": str(e)}
