from typing import Optional, Dict, Any
from db_utils import get_conn

SESSIONS_TABLE = "public.sessions"

def ensure_session_schema() -> None:
    stmt = f"""
    CREATE TABLE IF NOT EXISTS {SESSIONS_TABLE} (
      id SERIAL PRIMARY KEY,
      user_id TEXT NOT NULL,
      platform TEXT NOT NULL,
      last_activity_ts TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      has_greeted BOOLEAN NOT NULL DEFAULT FALSE,
      current_state TEXT NOT NULL DEFAULT 'idle',
      status TEXT NOT NULL DEFAULT 'pendiente',
      extra JSONB NOT NULL DEFAULT '{{}}'::jsonb,
      UNIQUE (user_id, platform)
    );
    CREATE INDEX IF NOT EXISTS idx_sessions_user_platform
      ON {SESSIONS_TABLE} (user_id, platform);
    """
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(stmt)
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

