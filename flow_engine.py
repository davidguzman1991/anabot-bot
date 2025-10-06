# flow_engine.py
# ------------------------------------------------------------------
# FlowEngine para AnaBot — contrato compatible con main:
# - Exponer .run(text, current_id) -> {"reply":[...], "next":"..."}
# - Cargar y normalizar flow.json (formato por nodos con reply/routes)
# - No hace I/O externo ni persiste estado
# ------------------------------------------------------------------
from __future__ import annotations

import json
import os
import re
import logging
from typing import Any, Dict, List, Optional

log = logging.getLogger("anabot.flow")

class FlowEngine:
    def __init__(self, flow_path: Optional[str] = None) -> None:
        self.flow_path = flow_path or os.getenv("FLOW_JSON_PATH", "flow.json")
        self.nodes: Dict[str, Dict[str, Any]] = {}
        self.start: str = "menu_principal"
        self._load()

    # --------------------------- Carga -----------------------------
    def _load(self) -> None:
        log.info("FLOW PATH=%s", self.flow_path)
        with open(self.flow_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        # Formato esperado: { "<node_id>": { "reply":[str], "routes":{ "1":"otro_nodo", ... } } }
        # Aceptamos rutas como dict o lista de objetos {key,next,label}
        normalized: Dict[str, Dict[str, Any]] = {}
        for node_id, node in data.items():
            nid = str(node_id).strip()
            reply = node.get("reply") or []
            routes = node.get("routes") or {}
            # normalizar a dict simple: key -> next
            if isinstance(routes, list):
                rmap = {}
                for r in routes:
                    k = str(r.get("key", "")).strip()
                    nx = str(r.get("next", "")).strip()
                    if k and nx:
                        rmap[k] = nx
                routes = rmap
            elif isinstance(routes, dict):
                routes = {str(k).strip(): str(v).strip() for k, v in routes.items()}
            else:
                routes = {}

            normalized[nid] = {"reply": list(reply), "routes": routes}

        self.nodes = normalized
        if "menu_principal" in self.nodes:
            self.start = "menu_principal"
        elif normalized:
            # primer nodo como inicio si no existe el esperado
            self.start = next(iter(normalized.keys()))
            log.warning("FLOW sin 'menu_principal', usando inicio=%s", self.start)
        else:
            log.error("FLOW vacío: no hay nodos")

        log.info("FLOW NODES=%d START=%s", len(self.nodes), self.start)

    # ---------------------------- Run ------------------------------
    def run(self, text: str, current_id: Optional[str]) -> Dict[str, Any]:
        """Devuelve {"reply":[...], "next":"..."} o {} si no puede rutear."""
        # normalizar entrada
        raw = (text or "").strip()
        msg = self._normalize_text(raw)

        # teclas globales
        if msg == "9" or not current_id:
            node_id = self.start
        elif msg == "0":
            # por simplicidad: volver al inicio
            node_id = self.start
        else:
            node_id = current_id

        node = self.nodes.get(node_id)
        if not node:
            log.warning("RUN: nodo inexistente=%s → forzar inicio", node_id)
            node_id = self.start
            node = self.nodes.get(node_id, {"reply": [], "routes": {}})

        # Si el usuario envía una opción válida, rutear
        routes = node.get("routes") or {}
        if msg in routes:
            next_id = routes[msg]
            next_node = self.nodes.get(next_id)
            if next_node:
                return {"reply": next_node.get("reply") or [], "next": next_id}
            else:
                log.warning("RUN: next_id=%s no existe; me quedo en %s", next_id, node_id)

        # Mostrar el nodo actual
        return {"reply": node.get("reply") or [], "next": node_id}

    # ------------------------ Utilidades ---------------------------
    _RE_MULTI = re.compile(r"\s+", re.MULTILINE)

    @classmethod
    def _normalize_text(cls, s: str) -> str:
        s2 = cls._RE_MULTI.sub(" ", s.strip().lower())
        # quitar tildes simples si quieres, por ahora minimalista
        return s2
