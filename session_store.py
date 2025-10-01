"""Session persistence helpers backed by Postgres."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Tuple

import psycopg2
from psycopg2.extras import Json, RealDictCursor

from config import get_settings

_SETTINGS = get_settings()
_DATABASE_URL = _SETTINGS.DATABASE_URL

DEFAULT_SESSION: Dict[str, Any] = {
    "state": "HOME",
    "stack": [],
    "payload": {},
    "patient_id": None,
    "has_greeted": False,
    "engine_state": {
        "node": "HOME",
        "history": [],
        "ctx": {},
        "_needs_on_enter": True,
        "inactivity_stage": 0,
        "last_activity": None,
    },
}


def _conn():
    if not _DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not configured")
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
                "SELECT state FROM sessions WHERE channel=%s AND user_key=%s",
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

    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO sessions (channel, user_key, state, updated_at)
                VALUES (%s, %s, %s::jsonb, %s)
                ON CONFLICT (channel, user_key)
                DO UPDATE SET state=EXCLUDED.state, updated_at=EXCLUDED.updated_at
                """,
                (channel, user_key, Json(normalized), now),
            )
        conn.commit()


def _persist_session(cur, channel: str, user_key: str, state: Dict[str, Any]) -> None:
    now = datetime.now(timezone.utc)
    cur.execute(
        """
        INSERT INTO sessions (channel, user_key, state, updated_at)
        VALUES (%s, %s, %s::jsonb, %s)
        ON CONFLICT (channel, user_key)
        DO UPDATE SET state=EXCLUDED.state, updated_at=EXCLUDED.updated_at
        """,
        (channel, user_key, Json(state), now),
    )


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

