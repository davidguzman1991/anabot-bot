import logging
slog = logging.getLogger("sessions")
# session_store.py — rewrite from scratch (robusto e idempotente)
from psycopg2 import Error as PGError
from typing import Optional, Dict, Any
from datetime import datetime, timezone
import json
from __future__ import annotations
import os
import logging
import psycopg2
from psycopg2.extras import RealDictCursor

log = logging.getLogger("anabot")

DATABASE_URL = os.getenv("DATABASE_URL")

SET user_id = COALESCE(
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

  user_id,
    cur.execute("""
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = %s AND column_name = %s
        """, (table, column))
    return cur.fetchone() is not None

  (extra ->> 'user_id'),
    """
    Crea la tabla public.sessions si no existe y asegura columnas/índices esperados.
    Idempotente.
    """
    with get_conn() as conn, conn.cursor() as cur:
        # 1) Tabla base
        cur.execute("""
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
        """)

        # 2) Columna 'canal' (clave para tu código)
        if not _column_exists(cur, "sessions", "canal"):
            log.info("schema: creando columna 'canal'…")
            cur.execute("ALTER TABLE public.sessions ADD COLUMN canal TEXT NOT NULL DEFAULT 'whatsapp';")

        # 3) Unique lógico por (user_id, platform)
        cur.execute("""
        DO $$
        BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM pg_indexes
            WHERE schemaname = 'public' AND indexname = 'sessions_user_platform_key'
          ) THEN
            EXECUTE 'CREATE UNIQUE INDEX sessions_user_platform_key ON public.sessions (user_id, platform)';
          END IF;
        END $$;
        """)

        # 4) Índice auxiliar nombrado (opcional si ya tienes el único)
        cur.execute("""
        DO $$
        BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM pg_indexes
            WHERE schemaname = 'public' AND indexname = 'idx_sessions_user_platform'
          ) THEN
            EXECUTE 'CREATE INDEX idx_sessions_user_platform ON public.sessions (user_id, platform)';
          END IF;
        END $$;
        """)

        # 5) Diagnóstico de columnas
        cur.execute("""
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema='public' AND table_name='sessions'
        ORDER BY ordinal_position
        """)
        cols = [r["column_name"] for r in cur.fetchall()]
        log.info("🧩 Columnas sessions: %s", ", ".join(cols))

  'unknown_' || substr(md5(random()::text || clock_timestamp()::text), 1, 12)
                   current_state: str, has_greeted: bool,
                   status: str = "ok", extra: dict | None = None,
                   canal: str = "whatsapp") -> None:
    """
    UPSERT idempotente. Actualiza last_activity_ts y columnas de estado.
    """
    if extra is None:
        extra = {}
    sql = """
    INSERT INTO public.sessions (user_id, platform, current_state, has_greeted, status, extra, last_activity_ts, canal)
    VALUES (%s, %s, %s, %s, %s, %s::jsonb, now(), %s)
    ON CONFLICT (user_id, platform) DO UPDATE
    SET current_state = EXCLUDED.current_state,
        has_greeted   = EXCLUDED.has_greeted,
        status        = EXCLUDED.status,
        extra         = EXCLUDED.extra,
        canal         = EXCLUDED.canal,
        last_activity_ts = now();
    """
    vals = (user_id, platform, current_state, has_greeted, status, psycopg2.extras.Json(extra), canal)
    try:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(sql, vals)
    except psycopg2.Error as e:
        log.error("sessions: UPSERT sessions falló | pgcode=%s | pgerror=%s", getattr(e, "pgcode", None), getattr(e, "pgerror", str(e)))
        raise
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
        slog.info("get_session user=%s platform=%s -> %s", user_id, platform, dict(row) if row else None)
        return dict(row) if row else None



def upsert_session(
  user_id: str,
  platform: str,
  current_state: str = "idle",
  *,
  has_greeted: bool | None = None,
  status: str | None = None,
  extra: dict | None = None,
):
  if not user_id or not platform:
    return

  canal = platform or "whatsapp"  # ← valor por defecto seguro
  has_greeted = bool(has_greeted) if has_greeted is not None else False
  status = status or "ok"
  extra_json = json.dumps(extra or {})

  # columnas en español que existen en tu tabla
  sql = """
  INSERT INTO public.sessions
    (canal, id_usuario, plataforma, estado_actual, ha_saludado, estado, extra, última_actividad_ts)
  VALUES
    (%s,     %s,         %s,         %s,           %s,           %s,     %s,    NOW())
  ON CONFLICT (id_usuario, plataforma)
  DO UPDATE SET
    canal = EXCLUDED.canal,
    estado_actual = EXCLUDED.estado_actual,
    ha_saludado   = EXCLUDED.ha_saludado,
    estado        = EXCLUDED.estado,
    extra         = EXCLUDED.extra,
    última_actividad_ts = NOW();
  """

  params = [canal, user_id, platform, current_state or "menu_principal",
        has_greeted, status, extra_json]

  from psycopg2 import Error as PGError
  slog = logging.getLogger("sessions")
  try:
    with get_conn() as conn, conn.cursor() as cur:
      cur.execute(sql, params)
      conn.commit()
      slog.info("UPSERT OK user=%s platform=%s state=%s canal=%s",
            user_id, platform, current_state, canal)
  except PGError as e:
    slog.exception("UPSERT sessions falló | pgcode=%s | pgerror=%s | sql=%s | params=%s",
             getattr(e, "pgcode", None), getattr(e, "pgerror", None), sql, params)
    raise


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

