from typing import Optional, Dict, Any
from db_utils import get_conn

SESSIONS_TABLE = "public.sessions"

def ensure_session_schema():
    ddl = """
    CREATE TABLE IF NOT EXISTS public.sessions (id SERIAL PRIMARY KEY);

    ALTER TABLE public.sessions
      ADD COLUMN IF NOT EXISTS user_id TEXT,
      ADD COLUMN IF NOT EXISTS platform TEXT,
      ADD COLUMN IF NOT EXISTS last_activity_ts TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      ADD COLUMN IF NOT EXISTS has_greeted BOOLEAN NOT NULL DEFAULT FALSE,
      ADD COLUMN IF NOT EXISTS current_state TEXT NOT NULL DEFAULT 'idle',
      ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'pendiente',
      ADD COLUMN IF NOT EXISTS extra JSONB NOT NULL DEFAULT '{}'::jsonb;

    -- Backfill mínimo para evitar NOT NULL violations
    UPDATE public.sessions SET platform = 'whatsapp' WHERE platform IS NULL;
    UPDATE public.sessions
      SET user_id = COALESCE(user_id, (extra ->> 'user_id'), ('unknown_' || id::text))
      WHERE user_id IS NULL;

    -- Impone NOT NULL solo si ya no hay NULLs
    DO $$
    DECLARE
      c1 int; c2 int;
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

    -- Índice único si no existe
    DO $$
    BEGIN
      IF NOT EXISTS (
        SELECT 1 FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE c.relkind = 'i'
          AND c.relname = 'idx_sessions_user_platform'
          AND n.nspname = 'public'
      ) THEN
        CREATE UNIQUE INDEX idx_sessions_user_platform ON public.sessions(user_id, platform);
      END IF;
    END$$;
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(ddl)
        conn.commit()

def get_session(user_id: str, platform: str = "whatsapp") -> Optional[Dict[str, Any]]:
    q = f"SELECT * FROM {SESSIONS_TABLE} WHERE user_id=%s AND platform=%s LIMIT 1"
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(q, (user_id, platform))
        return cur.fetchone()

def update_session(user_id: str, platform: str = "whatsapp", **kwargs) -> Dict[str, Any]:
    # Upsert por UNIQUE(user_id, platform)
    fields = ["user_id", "platform"]
    vals = [user_id, platform]
    sets = []
    for k, v in kwargs.items():
        fields.append(k)
        vals.append(v)
        sets.append(f"{k}=EXCLUDED.{k}")
    sql = f"""
    INSERT INTO {SESSIONS_TABLE} ({",".join(fields)})
    VALUES ({",".join(["%s"]*len(fields))})
    ON CONFLICT (user_id, platform) DO UPDATE SET
      last_activity_ts = NOW(){"," if sets else ""} {", ".join(sets)}
    RETURNING *;
    """
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(sql, vals)
        row = cur.fetchone()
        conn.commit()
        return row

def reset_session(user_id: str, platform: str = "whatsapp") -> Dict[str, Any]:
    # Resetea a valores por defecto
    kwargs = {
        "has_greeted": False,
        "current_state": "idle",
        "status": "pendiente",
        "extra": "{}"
    }
    return update_session(user_id, platform, **kwargs)

