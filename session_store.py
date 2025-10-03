def get_session(user_id: str) -> Dict[str, Any]:
def update_session(user_id: str, **kwargs) -> None:
def reset_session(user_id: str) -> None:
def _conn():

from typing import Optional, Dict, Any
from db_utils import get_conn

class SessionStore:
    TABLE = "public.sessions"

    def ensure_schema(self):
        stmt = """
        CREATE TABLE IF NOT EXISTS public.sessions (
          id SERIAL PRIMARY KEY,
          user_id TEXT NOT NULL,
          platform TEXT NOT NULL,
          last_activity_ts TIMESTAMPTZ NOT NULL DEFAULT NOW(),
          has_greeted BOOLEAN NOT NULL DEFAULT FALSE,
          current_state TEXT NOT NULL DEFAULT 'idle',
          status TEXT NOT NULL DEFAULT 'pendiente',
          extra JSONB NOT NULL DEFAULT '{}'::jsonb,
          UNIQUE (user_id, platform)
        );
        CREATE INDEX IF NOT EXISTS idx_sessions_user_platform
          ON public.sessions (user_id, platform);
        """
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(stmt)
            conn.commit()

    def get(self, user_id: str, platform: str) -> Optional[Dict[str, Any]]:
        q = f"SELECT * FROM {self.TABLE} WHERE user_id=%s AND platform=%s LIMIT 1"
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(q, (user_id, platform))
            return cur.fetchone()

    def upsert(self, user_id: str, platform: str, **kwargs) -> Dict[str, Any]:
        fields = ["user_id", "platform"]
        vals = [user_id, platform]
        sets = []
        for k, v in kwargs.items():
            fields.append(k)
            vals.append(v)
            sets.append(f"{k}=EXCLUDED.{k}")
        sql = f"""
        INSERT INTO {self.TABLE} ({",".join(fields)})
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
    return psycopg2.connect(_DATABASE_URL)


def _ensure_defaults(data: Dict[str, Any]) -> Dict[str, Any]:
    state = {**DEFAULT_SESSION}
    state.update({k: v for k, v in data.items() if k in state})
    if "has_greeted" not in state:
        state["has_greeted"] = False

    engine_state = data.get("engine_state") or {}
    merged_engine = {**DEFAULT_SESSION["engine_state"]}
    merged_engine.update(engine_state)
    # Keep node/history aligned with outer structure
    merged_engine["node"] = state.get("state", "HOME")
    merged_engine["history"] = state.get("stack", [])
    if state.get("payload"):
        merged_engine["ctx"] = state["payload"]
    state["engine_state"] = merged_engine
    return state


def load_session(channel: str, user_key: str) -> Dict[str, Any]:
    """Fetch or create the stored session payload."""
    with _conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT state FROM public.sessions WHERE channel=%s AND user_key=%s",
                (channel, user_key),
            )
            row = cur.fetchone()
            if not row:
                state = {**DEFAULT_SESSION}
                _persist_session(cur, channel, user_key, state)
                conn.commit()
                return state
            payload = row.get("state") or {}
            if isinstance(payload, str):
                payload = json.loads(payload)
            return _ensure_defaults(payload)


def save_session(channel: str, user_key: str, state_dict: Dict[str, Any]) -> None:
    """Upsert the session payload."""
    normalized = _ensure_defaults(state_dict)
    normalized["state"] = normalized.get("state") or normalized["engine_state"].get("node", "HOME")
    normalized["stack"] = normalized.get("stack") or normalized["engine_state"].get("history", [])
    normalized["payload"] = normalized.get("payload") or normalized["engine_state"].get("ctx", {})
    if "has_greeted" not in normalized:
        normalized["has_greeted"] = False
    now = datetime.now(timezone.utc)

    try:
        with _conn() as conn:
            with conn.cursor() as cur:
                sql = (
                    "INSERT INTO public.sessions (channel, user_key, state, updated_at) "
                    "VALUES (%s, %s, %s::jsonb, %s) "
                    "ON CONFLICT (channel, user_key) "
                    "DO UPDATE SET state=EXCLUDED.state, updated_at=EXCLUDED.updated_at"
                )
                logger = logging.getLogger("anabot")
                logger.info("UPSERT public.sessions columns=[channel, user_key, state, updated_at]")
                cur.execute(sql, (channel, user_key, Json(normalized), now))
            conn.commit()
    except Exception as e:
        logger = logging.getLogger("anabot")
        logger.exception("db error in save_session (upsert)")
        try:
            if conn:
                conn.rollback()
        except Exception:
            pass


def _persist_session(cur, channel: str, user_key: str, state: Dict[str, Any]) -> None:
    now = datetime.now(timezone.utc)
    sql = (
        "INSERT INTO public.sessions (channel, user_key, state, updated_at) "
        "VALUES (%s, %s, %s::jsonb, %s) "
        "ON CONFLICT (channel, user_key) "
        "DO UPDATE SET state=EXCLUDED.state, updated_at=EXCLUDED.updated_at"
    )
    logger = logging.getLogger("anabot")
    logger.info("UPSERT public.sessions columns=[channel, user_key, state, updated_at]")
    try:
        cur.execute(sql, (channel, user_key, Json(state), now))
    except Exception as e:
        logger.exception("db error in _persist_session (upsert)")


def push_state(session: Dict[str, Any], new_state: str) -> Dict[str, Any]:
    stack = session.setdefault("stack", [])
    current = session.get("state")
    if current:
        stack.append(current)
    session["state"] = new_state
    session.setdefault("engine_state", {}).update({"node": new_state, "history": stack})
    return session


def pop_state(session: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    stack = session.setdefault("stack", [])
    if stack:
        new_state = stack.pop()
    else:
        new_state = "HOME"
    session["state"] = new_state
    session.setdefault("engine_state", {}).update({"node": new_state, "history": stack})
    return new_state, session


@dataclass
class FlowSessionStore:
    """Adapter used by FlowEngine to persist state in Postgres."""

    def _split(self, sid: str) -> Tuple[str, str]:
        if ":" not in sid:
            raise ValueError("Session id must follow '<channel>:<user>' format")
        channel, user_key = sid.split(":", 1)
        return channel, user_key

    def get(self, sid: str) -> Dict[str, Any]:
        channel, user_key = self._split(sid)
        state = load_session(channel, user_key)
        engine_state = state.get("engine_state") or {}
        engine_state.setdefault("node", state.get("state", "HOME"))
        engine_state.setdefault("history", state.get("stack", []))
        engine_state.setdefault("ctx", state.get("payload", {}))
        engine_state.setdefault("_needs_on_enter", True)
        engine_state.setdefault("inactivity_stage", 0)
        if not engine_state.get("last_activity"):
            engine_state["last_activity"] = datetime.now(timezone.utc).isoformat()
        return engine_state

    def set(self, sid: str, data: Dict[str, Any]) -> None:
        channel, user_key = self._split(sid)
        serialized = {
            "state": data.get("node", "HOME"),
            "stack": data.get("history", []),
            "payload": data.get("ctx", {}),
            "patient_id": data.get("patient_id"),
            "engine_state": {
                "node": data.get("node", "HOME"),
                "history": data.get("history", []),
                "ctx": data.get("ctx", {}),
                "_needs_on_enter": data.get("_needs_on_enter", False),
                "inactivity_stage": data.get("inactivity_stage", 0),
                "last_activity": data.get("last_activity"),
            },
        }
        save_session(channel, user_key, serialized)

    def snapshot(self, sid: str) -> Dict[str, Any]:
        channel, user_key = self._split(sid)
        return load_session(channel, user_key)

