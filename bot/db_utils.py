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
            return
        with conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO conversation_logs(user_id, message, platform)
                VALUES (%s, %s, %s)
                """,
                (user_id, text or "", platform),
            )
    except Exception:
        logger.exception("save_message failed")


def save_response(user_id: str, text: str, platform: str):
    try:
        conn = _conn()
        if not conn:
            return
        with conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO conversation_logs(user_id, response, platform, status)
                VALUES (%s, %s, %s, %s)
                """,
                (user_id, text or "", platform, "pendiente"),
            )
    except Exception:
        logger.exception("save_response failed")


def log_handoff(user_id: str, last_text: str, platform: str = "wa"):
    try:
        conn = _conn()
        if not conn:
            return
        with conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO conversation_logs(user_id, message, platform, handoff, status)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (user_id, last_text or "", platform, True, "pendiente"),
            )
    except Exception:
        logger.exception("log_handoff failed")


def save_appointment(user_id: str, ts: str, status: str = "pendiente"):
    try:
        conn = _conn()
        if not conn:
            return
        with conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO appointments(user_id, appointment_date, status)
                VALUES (%s, %s, %s)
                """,
                (user_id, ts, status),
            )
    except Exception:
        logger.exception("save_appointment failed")
