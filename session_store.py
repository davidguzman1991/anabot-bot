import logging
slog = logging.getLogger("sessions")
from __future__ import annotations
# session_store.py — rewrite from scratch (robusto e idempotente)
from psycopg2 import Error as PGError
from typing import Optional, Dict, Any
from datetime import datetime, timezone
import json

from db_utils import get_conn

# =========================
#  Esquema y migraciones
# =========================
DDL = """
-- A) Crear tabla mínima si no existe
CREATE TABLE IF NOT EXISTS public.sessions (id BIGSERIAL);

-- B) Asegurar columna id y PK (si aún no existieran)
ALTER TABLE public.sessions
  ADD COLUMN IF NOT EXISTS id BIGSERIAL;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM information_schema.table_constraints
    WHERE table_schema='public'
      AND table_name='sessions'
      AND constraint_type='PRIMARY KEY'
  ) THEN
    ALTER TABLE public.sessions ADD PRIMARY KEY (id);
  END IF;
END$$;

-- C) Asegurar columnas requeridas
ALTER TABLE public.sessions
  ADD COLUMN IF NOT EXISTS user_id TEXT,
  ADD COLUMN IF NOT EXISTS platform TEXT,
  ADD COLUMN IF NOT EXISTS last_activity_ts TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  ADD COLUMN IF NOT EXISTS has_greeted BOOLEAN NOT NULL DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS current_state TEXT NOT NULL DEFAULT 'idle',
  ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'pendiente',
  ADD COLUMN IF NOT EXISTS extra JSONB NOT NULL DEFAULT '{}'::jsonb;

-- D) Backfill mínimo (nunca debe romper)
UPDATE public.sessions SET platform = 'whatsapp' WHERE platform IS NULL;
UPDATE public.sessions
SET user_id = COALESCE(
  user_id,
  (extra ->> 'user_id'),
  'unknown_' || substr(md5(random()::text || clock_timestamp()::text), 1, 12)
)
WHERE user_id IS NULL;

-- E) NOT NULL solo si es seguro
DO $$
DECLARE c1 int; c2 int;
BEGIN
  SELECT COUNT(*) INTO c1 FROM public.sessions WHERE user_id IS NULL;
  IF c1 = 0 THEN
    EXECUTE 'ALTER TABLE public.sessions ALTER COLUMN user_id SET NOT NULL';
  END IF;

  SELECT COUNT(*) INTO c2 FROM public.sessions WHERE platform IS NULL;
  IF c2 = 0 THEN
    EXECUTE 'ALTER TABLE public.sessions ALTER COLUMN platform SET NOT NULL';
  END IF;
END$$;

-- F) Índice único (clave natural)
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE c.relkind='i' AND c.relname='idx_sessions_user_platform' AND n.nspname='public'
  ) THEN
    CREATE UNIQUE INDEX idx_sessions_user_platform ON public.sessions(user_id, platform);
  END IF;
END$$;
"""

def ensure_session_schema() -> None:
    """Aplica el DDL idempotente; seguro en startup."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(DDL)
        conn.commit()

# =========================
#  Helpers CRUD de sesión
# =========================

def _now() -> datetime:
    return datetime.now(timezone.utc)

def get_session(user_id: str, platform: str) -> Optional[Dict[str, Any]]:
    """Obtiene una sesión o None."""
    sql = """
    SELECT id, user_id, platform, last_activity_ts, has_greeted, current_state, status, extra
    FROM public.sessions
    WHERE user_id = %s AND platform = %s
    """
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(sql, (user_id, platform))
        row = cur.fetchone()
        return dict(row) if row else None


def upsert_session(user_id: str, platform: str, current_state: str = "idle", channel: str = "whatsapp") -> Dict[str, Any]:
  """
  Inserta o actualiza (merge) una sesión.
  """
  # Sanity: si viene vacío, fuerza un valor
  channel = channel or platform or "whatsapp"

  sql_insert = """
  INSERT INTO public.sessions (user_id, platform, channel, last_activity_ts, has_greeted, current_state, status, extra)
  VALUES (%s, %s, %s, NOW(), FALSE, %s, 'pendiente', '{}'::jsonb)
  ON CONFLICT (user_id, platform)
  DO UPDATE SET
    channel = EXCLUDED.channel,
    last_activity_ts = NOW(),
    current_state = EXCLUDED.current_state
  ;
  """
  try:
    with get_conn() as conn, conn.cursor() as cur:
      cur.execute(sql_insert, (user_id, platform, channel, current_state))
      conn.commit()
  except PGError as e:
    slog.exception(
      "UPSERT sessions falló | pgcode=%s | pgerror=%s | sql=%s | params=%s",
      getattr(e, "pgcode", None),
      getattr(e, "pgerror", None),
      sql_insert,
      (user_id, platform, channel, current_state),
    )
    raise

  return get_session(user_id, platform)  # type: ignore[return-value]


def update_session(user_id: str, platform: str, **fields: Any) -> None:
    """Alias de upsert_session (actualiza siempre que exista; si no, crea)."""
    upsert_session(user_id, platform, **fields)

def touch_session(user_id: str, platform: str) -> None:
    """Solo actualiza el timestamp de actividad."""
    sql = """
    UPDATE public.sessions
    SET last_activity_ts = %s
    WHERE user_id = %s AND platform = %s
    """
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(sql, (_now(), user_id, platform))
        if cur.rowcount == 0:
            # Crea si no existe
            cur.execute(
                "INSERT INTO public.sessions (user_id, platform, last_activity_ts) VALUES (%s, %s, %s)",
                (user_id, platform, _now())
            )
        conn.commit()

def delete_session(user_id: str, platform: str) -> int:
    """Elimina una sesión. Devuelve # filas afectadas (0/1)."""
    sql = "DELETE FROM public.sessions WHERE user_id = %s AND platform = %s"
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(sql, (user_id, platform))
        count = cur.rowcount
        conn.commit()
        return count

