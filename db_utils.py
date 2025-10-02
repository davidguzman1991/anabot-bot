import logging
import os
from typing import Optional

import psycopg2
from psycopg2 import extensions

logger = logging.getLogger("anabot")

DATABASE_URL = os.getenv("DATABASE_URL")


def _conn() -> Optional[extensions.connection]:
    if not DATABASE_URL:
        logger.warning("DATABASE_URL not set; skipping DB writes.")
        return None
    return psycopg2.connect(DATABASE_URL)


def save_message(user_id: str, text: str, platform: str):
    try:
        conn = _conn()
        if not conn:
            return False
        with conn:
            with conn.cursor() as cur:
                sql = "INSERT INTO public.conversation_logs (user_id, message, platform) VALUES (%s, %s, %s)"
                logger.info("INSERT public.conversation_logs columns=[user_id, message, platform]")
                cur.execute(sql, (user_id, text or "", platform))
            conn.commit()
        return True
    except Exception as e:
        logger.exception("db error in save_message")
        try:
            if conn:
                conn.rollback()
        except Exception:
            pass
        return False


def save_response(user_id: str, text: str, platform: str):
    try:
        conn = _conn()
        if not conn:
            return False
        with conn:
            with conn.cursor() as cur:
                # status y handoff pueden ser opcionales
                sql = "INSERT INTO public.conversation_logs (user_id, response, platform, status) VALUES (%s, %s, %s, %s)"
                logger.info("INSERT public.conversation_logs columns=[user_id, response, platform, status]")
                cur.execute(sql, (user_id, text or "", platform, "pendiente"))
            conn.commit()
        return True
    except Exception as e:
        logger.exception("db error in save_response")
        try:
            if conn:
                conn.rollback()
        except Exception:
            pass
        return False


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
