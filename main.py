# main.py — FastAPI entrypoint para AnaBot

from __future__ import annotations

import os
import logging
from typing import Any, Dict, Optional, Tuple

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, PlainTextResponse

from session_store import ensure_session_schema, get_conn, upsert_session, touch_session

log = logging.getLogger("anabot")
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))

# === Config ===
WHATSAPP_VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN", "verify-token-dev")
ENV = os.getenv("ENV", "production")

app = FastAPI(title="AnaBot", version="1.0.0")

# -------------------------------------------------------------------
# Hooks (lazy import para evitar ciclos)
# -------------------------------------------------------------------
_hooks: Optional["Hooks"] = None

def get_hooks():
    global _hooks
    if _hooks is None:
        # Import aquí para evitar circular imports si hooks.py importa algo de main
        from hooks import Hooks
        _hooks = Hooks()
    return _hooks

# -------------------------------------------------------------------
# Utilidades
# -------------------------------------------------------------------
def _db_boot_log() -> None:
    """Loguea DB y columnas de sessions (útil para depurar)."""
    try:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT current_database() AS db, current_user AS usr;")
            row = cur.fetchone()
            db, usr = row["db"], row["usr"]

            cur.execute("SELECT inet_server_addr()::text AS host;")
            host = (cur.fetchone() or {}).get("host")

            cur.execute("""
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema='public' AND table_name='sessions'
                ORDER BY ordinal_position;
            """)
            cols = [r["column_name"] for r in cur.fetchall()]

            log.info("✅ DB conectada: %s | schema: public | host: %s", db, host)
            log.info("🧩 Columns sessions: %s", ", ".join(cols))
    except Exception as e:
        log.exception("No se pudo loguear metadata de DB: %s", e)

def _extract_wa_message(body: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    """Extrae (user_id, text) desde el payload de WhatsApp Cloud API."""
    user_id = body.get("from") or body.get("user") or body.get("user_id")
    text = body.get("text") or body.get("message") or body.get("body")

    if user_id and (text is not None):
        return str(user_id), str(text)

    # Estructura oficial de Meta
    try:
        entry = body.get("entry", [])[0]
        changes = entry.get("changes", [])[0]
        value = changes.get("value", {}) or {}

        contacts = value.get("contacts") or []
        if contacts:
            user_id = contacts[0].get("wa_id") or user_id

        messages = value.get("messages") or []
        if messages:
            msg = messages[0]
            if "text" in msg and isinstance(msg["text"], dict):
                text = msg["text"].get("body", text)
            elif "button" in msg:
                text = msg["button"].get("text") or msg.get("interactive", {}).get("button_reply", {}).get("title")
            elif "interactive" in msg:
                inter = msg["interactive"]
                text = (
                    inter.get("button_reply", {}).get("title")
                    or inter.get("list_reply", {}).get("title")
                    or text
                )
    except Exception:
        pass

    return (str(user_id) if user_id is not None else None,
            str(text) if text is not None else None)

# -------------------------------------------------------------------
# Startup
# -------------------------------------------------------------------
@app.on_event("startup")
def on_startup() -> None:
    ensure_session_schema()
    _db_boot_log()

# -------------------------------------------------------------------
# Endpoints
# -------------------------------------------------------------------
@app.get("/")
def root() -> Dict[str, Any]:
    return {"ok": True, "service": "anabot", "env": ENV}

@app.get("/health")
def health() -> Dict[str, Any]:
    return {"ok": True}

@app.get("/health/db")
def health_db() -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    try:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT current_database() AS db, current_user AS usr;")
            row = cur.fetchone()
            out["db"] = row["db"]; out["user"] = row["usr"]
            cur.execute("""
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema='public' AND table_name='sessions'
                ORDER BY ordinal_position;
            """)
            out["sessions_columns"] = [r["column_name"] for r in cur.fetchall()]
    except Exception as e:
        out["error"] = str(e)
    return out

# Verificación de Meta (GET)
@app.get("/webhook/whatsapp")
def wa_verify(hub_mode: Optional[str] = None,
              hub_verify_token: Optional[str] = None,
              hub_challenge: Optional[str] = None):
    if hub_mode == "subscribe" and hub_verify_token == WHATSAPP_VERIFY_TOKEN:
        return PlainTextResponse(content=hub_challenge or "")
    return PlainTextResponse(content="forbidden", status_code=403)

# Recepción de mensajes (POST)
@app.post("/webhook/whatsapp")
async def wa_webhook(req: Request) -> Response:
    try:
        body: Dict[str, Any] = await req.json()
    except Exception:
        body = {}

    user_id, text = _extract_wa_message(body)
    if not user_id:
        log.warning("Webhook WA sin user_id | body=%s", body)
        return JSONResponse({"ok": True})  # 200 para que Meta no reintente

    if text is None:
        touch_session(user_id, "whatsapp")
        return JSONResponse({"ok": True})

    # upsert sesión
    try:
        upsert_session(
            user_id=user_id,
            platform="whatsapp",
            current_state="idle",
            has_greeted=False,
            status="ok",
            extra={},
            canal="whatsapp",
        )
    except Exception as e:
        log.exception("UPSERT sessions falló en webhook: %s", e)
        return JSONResponse({"ok": True, "error": "db"}, status_code=200)

    # Lógica del bot
    try:
        reply = get_hooks().handle_incoming_text(user_id, "whatsapp", text)
        return JSONResponse({"ok": True, "reply": reply})
    except Exception as e:
        log.exception("handler WA falló: %s", e)
        return JSONResponse({"ok": True, "error": "internal"}, status_code=200)

# Failsafe global
@app.exception_handler(Exception)
async def _unhandled_ex_handler(_: Request, exc: Exception):
    log.exception("Excepción no controlada: %s", exc)
    return JSONResponse({"ok": True, "error": "unhandled"}, status_code=200)
