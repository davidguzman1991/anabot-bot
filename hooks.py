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
        # 1) cargar estado
        sess = get_session(user_id, platform)
        current_node = (sess or {}).get("current_state")

        # 2) intentar transicionar con lo que llegó
        out = self.engine.run(text=text or "", current_id=current_node)

        # 3) si no hubo match, fuerza menú del nodo actual/start
        if not out:
            out = self.engine.run(text="", current_id=current_node) or self.engine.run(text="", current_id=None)

        # 4) persistir y responder (o fallback)
        if out:
            upsert_session(user_id, platform, current_state=out["next"], channel=platform)
            touch_session(user_id, platform)
            return "\n".join(out["reply"])

        return FALLBACK







