"""
Entrypoint principal para AnaBot — versión mínima y limpia.
- Carga FlowEngine con flow.json
- Asegura esquema de sessions
- Webhook de WhatsApp: extrae texto, consulta flujo y responde
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, Tuple

import httpx
from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from db_utils import wait_for_db
from session_store import ensure_session_schema
from flow_engine import FlowEngine
from hooks import Hooks

log = logging.getLogger("anabot")
logging.basicConfig(level=logging.INFO)

# ---------- Config ----------
FLOW_PATH = Path(__file__).with_name("flow.json")

WA_TOKEN = os.getenv("WHATSAPP_TOKEN", "")
WA_PHONE_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")
WA_VERIFY = os.getenv("WHATSAPP_VERIFY_TOKEN", "")
WA_MSG_URL = "https://graph.facebook.com/v20.0/{phone_id}/messages"

FOOTER = "\n\n0 Atrás · 9 Inicio · 00 Humano"

def add_footer(txt: str) -> str:
    txt = (txt or "").strip() or "Gracias por escribirnos."
    return txt if FOOTER.strip() in txt else f"{txt}{FOOTER}"

# ---------- App ----------
app = FastAPI(title="AnaBot", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_headers=["*"],
    allow_methods=["*"],
)

ENGINE: FlowEngine | None = None
HOOKS: Hooks | None = None

def get_engine() -> FlowEngine:
    global ENGINE
    if ENGINE is None:
        ENGINE = FlowEngine(flow_path=str(FLOW_PATH))
    return ENGINE

def get_hooks() -> Hooks:
    global HOOKS
    if HOOKS is None:
        HOOKS = Hooks(get_engine())
    return HOOKS

@app.on_event("startup")
def bootstrap():
    wait_for_db()
    ensure_session_schema()
    # precargar engine para validar flow.json al arranque
    eng = get_engine()
    log.info("Flow cargado: nodes=%s edges=%s start=%s", len(eng.graph["nodes"]), len(eng.graph["edges"]), eng.start_id)

@app.get("/health")
def health():
    return {"ok": True}

@app.api_route("/webhook", methods=["GET", "POST"], include_in_schema=False)
async def noop() -> Response:
    return Response(status_code=200)

# ---------- WhatsApp: verificación (GET) ----------
@app.get("/webhook/whatsapp")
async def wa_verify(
    mode: str | None = Query(None, alias="hub.mode"),
    challenge: str | None = Query(None, alias="hub.challenge"),
    token: str | None = Query(None, alias="hub.verify_token"),
):
    if (mode or "").strip() == "subscribe" and (token or "").strip() == (WA_VERIFY or "").strip():
        return int(challenge) if (challenge or "").isdigit() else (challenge or "")
    raise HTTPException(status_code=403, detail="Verification failed")

# ---------- WhatsApp: recepción (POST) ----------
def extract_wa(body: Dict[str, Any]) -> Tuple[str, str]:
    """Devuelve (user_id, text) desde el payload de WA. Si no hay texto, text=""."""
    try:
        msg = body["entry"][0]["changes"][0]["value"]["messages"][0]
        user_id = msg.get("from") or ""
        t = msg.get("type")
        text = ""
        if t == "text":
            text = (msg.get("text") or {}).get("body", "") or ""
        elif t == "button":
            text = (msg.get("button") or {}).get("text", "") or ""
        elif t == "interactive":
            it = msg.get("interactive") or {}
            text = (it.get("button_reply") or {}).get("title", "") \
                or (it.get("list_reply") or {}).get("title", "") \
                or ""
        return user_id, text.strip()
    except Exception:
        return "", ""

async def wa_send(to_number: str, text: str) -> None:
    if not (WA_TOKEN and WA_PHONE_ID):
        log.error("NO WA_TOKEN / WA_PHONE_ID — no se puede responder por WhatsApp")
        return
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post(
            WA_MSG_URL.format(phone_id=WA_PHONE_ID),
            headers={"Authorization": f"Bearer {WA_TOKEN}", "Content-Type": "application/json"},
            json={"messaging_product": "whatsapp", "to": to_number, "type": "text", "text": {"body": text}},
        )
        try:
            r.raise_for_status()
        except httpx.HTTPStatusError as exc:
            log.error("WA send error %s %s", exc.response.status_code if exc.response else "?", exc.response.text if exc.response else exc)

@app.post("/webhook/whatsapp")
async def wa_webhook(request: Request):
    try:
        body = await request.json()
    except Exception:
        # Meta a veces manda pings con form-data vacío
        body = {}
    try:
        # Puede llegar lote de mensajes; procesamos cada uno
        msgs = (body.get("entry") or [{}])[0].get("changes", [{}])[0].get("value", {}).get("messages", []) or []
        for _ in msgs:
            user_id, user_text = extract_wa(body)
            if not user_id:
                continue
            try:
                reply = get_hooks().handle_incoming_text(user_id, "whatsapp", user_text)
            except Exception:
                log.exception("handler WA falló")
                reply = "Estoy procesando tu mensaje. Por favor, intenta nuevamente en unos minutos."
            if reply:
                await wa_send(user_id, add_footer(reply))
    except Exception:
        log.exception("webhook WA falló")
    return {"ok": True}











