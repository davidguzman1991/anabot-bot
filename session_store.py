# session_store.py — versión estable y idempotente
from __future__ import annotations

import os
import json
import logging
from typing import Optional, Dict, Any
from datetime import datetime, timezone

import psycopg2
from psycopg2 import Error as PGError
from psycopg2.extras import RealDictCursor, Json

log = logging.getLogger("anabot")

# ----------------------------------------------------------------------
# Conexión
# ----------------------------------------------------------------------
DATABASE_URL = os.getenv("DATABASE_URL")

def get_conn():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL no está configurado")
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

# ----------------------------------------------------------------------
# Utilidades de esquema
# ----------------------------------------------------------------------
def _column_exists(cur, table: str, column: str) -> bool:
    cur.execute(
        """
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name   = %s
          AND column_name  = %s
        """,
        (table, column),
    )
    return cur.fetchone() is not None

def ensure_session_schema() -> None:
    """
    Crea/ajusta la tabla public.sessions de forma idempotente.
    Columnas objetivo:
      id (PK), user_id, platform, current_state, has_greeted, status,
      extra (jsonb), last_activity_ts (timestamptz), canal (text)
    Índice único en (user_id, platform)
    """
    with get_conn() as conn, conn.cursor() as cur:
        # 1) Tabla base
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS public.sessions (
              id               SERIAL PRIMARY KEY,
              user_id          TEXT NOT NULL,
              platform         TEXT NOT NULL,
              current_state    TEXT NOT NULL DEFAULT 'idle',
              has_greeted      BOOLEAN NOT NULL DEFAULT FALSE,
              status           TEXT NOT NULL DEFAULT 'ok',
              extra            JSONB NOT NULL DEFAULT '{}'::jsonb,
              last_activity_ts TIMESTAMPTZ NOT NULL DEFAULT now()
            );
            """
        )

        # 2) Columna 'canal' (necesaria para WhatsApp/Telegram)
        if not _column_exists(cur, "sessions", "canal"):
            log.info("schema: agregando columna 'canal'…")
            cur.execute(
                "ALTER TABLE public.sessions ADD COLUMN canal TEXT NOT NULL DEFAULT 'whatsapp';"
            )

        # 3) Índice único lógico por (user_id, platform)
        cur.execute(
            """
            DO $$
            BEGIN
              IF NOT EXISTS (
                SELECT 1
                FROM pg_indexes
                WHERE schemaname = 'public'
                  AND indexname  = 'sessions_user_platform_key'
              ) THEN
                EXECUTE 'CREATE UNIQUE INDEX sessions_user_platform_key ON public.sessions (user_id, platform)';
              END IF;
            END $$;
            """
        )

        conn.commit()
        log.info("schema: ensure_session_schema() OK")

# ----------------------------------------------------------------------
# Helpers CRUD de sesión
# ----------------------------------------------------------------------
def _now() -> datetime:
    return datetime.now(timezone.utc)

def get_session(user_id: str, platform: str) -> Optional[Dict[str, Any]]:
    """
    Devuelve la fila de sesión como dict o None.
    """
    sql = """
        SELECT id, user_id, platform, current_state, has_greeted,
               status, extra, last_activity_ts, canal
        FROM public.sessions
        WHERE user_id = %s AND platform = %s
        """
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(sql, (user_id, platform))
        row = cur.fetchone()
        return dict(row) if row else None

def _has_column(conn, table: str, column: str) -> bool:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT 1
            FROM information_schema.columns
            WHERE table_schema='public' AND table_name=%s AND column_name=%s
        """, (table, column))
        return cur.fetchone() is not None
def upsert_session(
    user_id: str,
    platform: str,
    current_state: str,
    has_greeted: bool,
    status: str = "ok",
    extra: Optional[Dict[str, Any]] = None,
    canal: str = "whatsapp",
) -> None:
    """
    Inserta o actualiza (user_id, platform) y refresca last_activity_ts.
    Requiere índice único en (user_id, platform).
    """
    if not canal:
        canal = platform or "whatsapp"

    def upsert_session(
        user_id: str,
        platform: str,
        current_state: str,
        has_greeted: bool,
        status: str = "ok",
        extra: Optional[Dict[str, Any]] = None,
        canal: str = "whatsapp",
    ) -> None:
        """Inserta/actualiza la sesión. Usa 'canal' y también 'user_key' (igual al user_id)."""
        if not canal:
            canal = platform or "whatsapp"

        payload_extra = Json(extra or {})

        sql = """
            INSERT INTO public.sessions
                (user_id, platform, current_state, has_greeted, status, extra, last_activity_ts, canal, user_key)
            VALUES
                (%s, %s, %s, %s, %s, %s::jsonb, NOW(), %s, %s)
            ON CONFLICT (user_id, platform)
            DO UPDATE SET
                current_state    = EXCLUDED.current_state,
                has_greeted      = EXCLUDED.has_greeted,
                status           = EXCLUDED.status,
                extra            = EXCLUDED.extra,
                last_activity_ts = NOW(),
                canal            = EXCLUDED.canal,
                user_key         = EXCLUDED.user_key;
        """
        vals = (user_id, platform, current_state, has_greeted, status, payload_extra, canal, user_id)

        conn = get_conn()
        try:
            with conn, conn.cursor() as cur:
                cur.execute(sql, vals)
        finally:
            conn.close()
    conn = get_conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE public.sessions
                    SET last_activity_ts = NOW()
                    WHERE user_id = %s AND platform = %s
                    """,
                    (user_id, platform),
                )
                if cur.rowcount == 0:
                    cur.execute(
                        """
                        INSERT INTO public.sessions (user_id, platform, last_activity_ts, canal)
                        VALUES (%s, %s, NOW(), %s)
                        """,
                        (user_id, platform, platform or "whatsapp"),
                    )
    finally:
        conn.close()

def delete_session(user_id: str, platform: str) -> int:
    """
    Elimina la sesión. Devuelve el número de filas afectadas.
    """
    conn = get_conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM public.sessions WHERE user_id = %s AND platform = %s",
                    (user_id, platform),
                )
                return cur.rowcount
    finally:
        conn.close()
