import logging
flog = logging.getLogger("flow")
# flow_engine.py  —  rewrite from scratch (robusto e idempotente)
from __future__ import annotations
from pathlib import Path
from typing import Dict, Any, List, Optional
import json
import logging

log = logging.getLogger("flow")
log.setLevel(logging.INFO)


class FlowEngine:
    """
    Motor de flujos muy simple:
      - Carga flow.json (tolerante a variaciones).
      - Garantiza start_node (defaultStartNode / startNode / primer nodo).
      - Normaliza edges: source/target|from/to + cond/condition/label.
      - run(text, current_id) -> {"reply": [str,...], "next": node_id} o {} si no hay flow.
      - Genera menú auto: payload.menu -> ["Opción A", "Opción B"] -> "1) Opción A\n2) Opción B"
    """

    def __init__(self, flow_path: str = "flow.json") -> None:
        self.flow_path = flow_path
        self.flow: Dict[str, Any] = {}
        self.nodes: Dict[str, Dict[str, Any]] = {}
        self.edges: List[Dict[str, Any]] = []
        self.start_node: Optional[str] = None
        self._load()

    # ---------- carga y normalización ----------

    def _load(self) -> None:
        p = Path(self.flow_path)
        if not p.exists():
            log.warning("[flow] %s no existe; flujo vacío", self.flow_path)
            self.flow = {"nodes": [], "edges": [], "defaultStartNode": None}
            self.nodes, self.edges, self.start_node = {}, [], None
            return

        try:
            self.flow = json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:
            log.error("[flow] no pude leer %s: %s", self.flow_path, e)
            self.flow = {"nodes": [], "edges": []}

        raw_nodes = self.flow.get("nodes", []) or []
        self.nodes = {}
        for n in raw_nodes:
            # Acepta {"id","label","payload"} o {"data":{"id","label","payload"}}
            data = n.get("data") or {}
            nid = n.get("id") or data.get("id")
            if not nid:
                continue
            label = (n.get("label") or data.get("label") or "").strip()
            payload = n.get("payload") or data.get("payload") or {}
            self.nodes[nid] = {"id": nid, "label": label, "payload": payload}

        # start node: defaultStartNode / startNode / primer nodo
        self.start_node = (
            self.flow.get("defaultStartNode")
            or self.flow.get("startNode")
            or (raw_nodes[0]["id"] if raw_nodes else None)
        )
        if self.start_node and self.start_node not in self.nodes:
            # si el id declarado no existe, usa primer nodo válido
            self.start_node = next(iter(self.nodes.keys()), None)

        # edges normalizados
        self.edges = []
        for e in self.flow.get("edges", []) or []:
            src = e.get("source") or e.get("from")
            dst = e.get("target") or e.get("to")
            cond = e.get("cond") or e.get("condition") or e.get("label") or ""
            if not (src and dst):
                continue
            self.edges.append(
                {"source": src, "target": dst, "cond": (cond or "").strip().lower()}
            )

        log.info(
            "[flow] cargado: nodes=%d, edges=%d, start=%s",
            len(self.nodes),
            len(self.edges),
            self.start_node,
        )

    # ---------- utilidades internas ----------

    @staticmethod
    def _menu_text(node: Dict[str, Any]) -> str:
        """Construye el texto a mostrar para un nodo (label + menú numerado)."""
        label = (node.get("label") or "").strip()
        payload = node.get("payload") or {}
        menu = payload.get("menu") or []
        parts: List[str] = []
        if label:
            parts.append(label)
        for i, opt in enumerate(menu, start=1):
            parts.append(f"{i}) {opt}")
        return "\n".join(parts) if parts else (label or "")

    def _next_by_text(self, current_id: str, text: str) -> Optional[str]:
        """Match por condición exacta (case-insensitive) o por número de opción."""
        t = (text or "").strip().lower()
        if not t:
            return None
        for e in self.edges:
            if e["source"] != current_id:
                continue
            c = e["cond"]
            if not c:
                continue
            if t == c:
                return e["target"]
            # coincidir números: "1", "2", ... si la condición es "1", "2", etc.
            if c[:1].isdigit() and t == c[:1]:
                return e["target"]
        return None

    # ---------- API pública ----------

    def run(self, text: str, current_id: Optional[str]) -> Dict[str, Any]:
        flog.info("[ENGINE] state=%s node=%s input=%s", current_id, current_id or self.start_node, text)
        """
        Ejecuta una transición en el flujo.
        :return: {"reply": [str,...], "next": node_id} o {} si no hay flujo cargado.
        """
        if not self.nodes or not self.start_node:
            return {}

        node_id = current_id or self.start_node
        if node_id not in self.nodes:
            node_id = self.start_node

        dest = self._next_by_text(node_id, text)
        if dest is None:
            # sin transición: mostrar menú del nodo actual
            node = self.nodes.get(node_id)
            if not node:
                return {}
            reply = self._menu_text(node)
            flog.info("[ENGINE] next=%s reply=%s", node_id, reply)
            return {"reply": [reply], "next": node_id}

        node = self.nodes.get(dest)
        if not node:
            # transición a nodo inexistente: quedarse donde está y mostrar su menú
            node = self.nodes.get(node_id)
            if not node:
                return {}
            reply = self._menu_text(node)
            flog.info("[ENGINE] next=%s reply=%s", node_id, reply)
            return {"reply": [reply], "next": node_id}

        # transición válida: mostrar el menú del destino
        reply = self._menu_text(node)
        flog.info("[ENGINE] next=%s reply=%s", node["id"], reply)
        return {"reply": [reply], "next": node["id"]}

    # ---------- helpers opcionales ----------

    def reload(self) -> None:
        """Recarga el flow.json desde disco (por si lo editas en caliente)."""
        self._load()

    def has_flow(self) -> bool:
        return bool(self.nodes) and bool(self.start_node)
