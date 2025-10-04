import logging
log = logging.getLogger("flow")
log.setLevel(logging.INFO)
# hooks.py — versión mínima y robusta
from typing import Any, Dict, Optional
from session_store import get_session, upsert_session, touch_session

FALLBACK = "Estoy procesando tu mensaje. Por favor, intenta nuevamente en unos minutos."

class Hooks:
    """
    Orquesta el flujo con el estado guardado en la tabla sessions.
    - Lee estado actual
    - Ejecuta el FlowEngine
    - Persiste el siguiente estado
    - Devuelve el texto de respuesta
    """
    def __init__(self, engine):
        self.engine = engine

    def handle_incoming_text(self, user_id: str, platform: str, text: str) -> str:
        # Log de entrada
        log.info("[FLOW] IN user=%s platform=%s text=%s", user_id, platform, text)

        # 1) cargar estado
        session = get_session(user_id, platform) or {}

        # Estado inicial: si current_state está vacío o inválido, normaliza y persiste
        curr = (session.get("current_state") or "").strip().lower()
        if curr in ("", "pendiente", "idle", "unknown", None):
            log.info("[FLOW] Estado inicial inválido (%s) → set menu_principal", curr)
            session["current_state"] = "menu_principal"
            upsert_session(
                user_id=user_id,
                platform=platform,
                current_state=session["current_state"],
                has_greeted=session.get("has_greeted", False),
                status=session.get("status", "ok"),
                extra=session.get("extra", {}),
            )

        # Log antes de motor
        log.info("[FLOW] BEFORE engine user=%s state=%s", user_id, session.get("current_state"))

        # 2) intentar transicionar con lo que llegó
        out = self.engine.run(text=text or "", current_id=session.get("current_state"))

        # 3) si no hubo match, fuerza menú del nodo actual/start
        if not out:
            out = self.engine.run(text="", current_id=session.get("current_state")) or self.engine.run(text="", current_id=None)

        # Log después de motor
        next_node = out["next"] if out and "next" in out else None
        log.info("[FLOW] AFTER engine user=%s next=%s", user_id, next_node)

        # 4) persistir y responder (o fallback)
        if out:
            session["current_state"] = out["next"]
            upsert_session(
                user_id=user_id,
                platform=platform,
                current_state=session["current_state"],
                has_greeted=session.get("has_greeted", False),
                status=session.get("status", "ok"),
                extra=session.get("extra", {}),
            )
            log.info("[FLOW] OUT user=%s state=%s", user_id, session["current_state"])
            touch_session(user_id, platform)
            return "\n".join(out["reply"])

        return FALLBACK







