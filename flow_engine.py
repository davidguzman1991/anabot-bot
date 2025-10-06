# flow_engine.py
def _normalize_text(s: str) -> str:
from __future__ import annotations

import json
import os
import re
import logging
from typing import Any, Dict, List, Optional

log = logging.getLogger("anabot.flow")

# ------------------------------- Utilidades -----------------------------------

def _normalize_text(s: str) -> str:
    s = (s or "").strip().lower()
    # Quitar tildes simples
    rep = (("á", "a"), ("é", "e"), ("í", "i"), ("ó", "o"), ("ú", "u"))
    for a, b in rep:
        s = s.replace(a, b)
    s = re.sub(r"\s+", " ", s)
    return s

# ------------------------------- Intenciones ----------------------------------

INTENTS = {
    "servicios": {
        "keys": ["s", "se", "servicio", "servicios", "precio", "valor", "costo", "duracion",
                 "ecg", "electro", "nutricion", "plan", "neuropatia", "pie diabetico",
                 "guayaquil", "milagro", "direccion", "ubicacion", "mapa"]
    },
    "agendar":   {
        "keys": ["a", "ag", "agendar", "agenda", "cita", "sacar cita", "sacar turno", "turno",
                 "reservar", "reserva", "hacer cita", "programar", "pedir cita",
                 "asendar", "ajendar", "ajendarme"]
    },
    "reagendar": {
        "keys": ["r", "rg", "reagendar", "cambiar hora", "mover cita", "posponer",
                 "reprogramar", "modificar cita"]
    },
    "cancelar":  {
        "keys": ["cancelar", "anular", "borrar cita", "ya no", "no puedo ir", "suspender"]
    },
    "consultar": {
        "keys": ["c", "cc", "consultar", "ver cita", "tengo cita", "confirmar hora",
                 "a que hora es", "cuando es mi cita", "detalles de mi cita", "donde es mi cita"]
    },
    "hablar":    {
        "keys": ["h", "dr", "hablar con doctor", "hablar con el dr", "hablar con guzman",
                 "medico", "humano", "asesor", "whatsapp del doctor", "numero del dr",
                 "comunicarme con el doctor", "llamar al medico", "mensaje para el doctor"]
    },
def _infer_intent(text: str) -> Optional[str]:

    "inicio":    {"keys": ["9", "i", "in", "inicio", "menu", "comenzar", "empezar", "home"]},
    "atras":     {"keys": ["0", "b", "atr", "atras", "volver", "regresar", "retroceder"]},
}

def _infer_intent(text: str) -> Optional[str]:
    t = _normalize_text(text)
    # Numérico directo
    if re.fullmatch(r"[1-5]", t):
        return t
    # Atajos globales
    if t in INTENTS["inicio"]["keys"]:
        return "9"
    if t in INTENTS["atras"]["keys"]:
        return "0"
    # Palabras clave
    for intent, cfg in INTENTS.items():
        if intent in ("inicio", "atras"):
            continue
        for k in cfg["keys"]:
            if k in t:
                return intent
    return None


# ------------------------------- Utilidades -----------------------------------
def _normalize_text(s: str) -> str:
    s = (s or "").strip().lower()
    # Quitar tildes simples
    rep = (("á", "a"), ("é", "e"), ("í", "i"), ("ó", "o"), ("ú", "u"))
    for a, b in rep:
        s = s.replace(a, b)
    s = re.sub(r"\s+", " ", s)
    return s

# ------------------------------- Intenciones ----------------------------------
INTENTS = {
    "servicios": {
        "keys": ["s", "se", "servicio", "servicios", "precio", "valor", "costo", "duracion",
                 "ecg", "electro", "nutricion", "plan", "neuropatia", "pie diabetico",
                 "guayaquil", "milagro", "direccion", "ubicacion", "mapa"]
    },
    "agendar":   {
        "keys": ["a", "ag", "agendar", "agenda", "cita", "sacar cita", "sacar turno", "turno",
                 "reservar", "reserva", "hacer cita", "programar", "pedir cita",
                 "asendar", "ajendar", "ajendarme"]
    },
    "reagendar": {
        "keys": ["r", "rg", "reagendar", "cambiar hora", "mover cita", "posponer",
                 "reprogramar", "modificar cita"]
    },
    "cancelar":  {
        "keys": ["cancelar", "anular", "borrar cita", "ya no", "no puedo ir", "suspender"]
    },
    "consultar": {
        "keys": ["c", "cc", "consultar", "ver cita", "tengo cita", "confirmar hora",
                 "a que hora es", "cuando es mi cita", "detalles de mi cita", "donde es mi cita"]
    },
    "hablar":    {
        "keys": ["h", "dr", "hablar con doctor", "hablar con el dr", "hablar con guzman",
                 "medico", "humano", "asesor", "whatsapp del doctor", "numero del dr",
                 "comunicarme con el doctor", "llamar al medico", "mensaje para el doctor"]
    },
    "inicio":    {"keys": ["9", "i", "in", "inicio", "menu", "comenzar", "empezar", "home"]},
    "atras":     {"keys": ["0", "b", "atr", "atras", "volver", "regresar", "retroceder"]},
}

def _infer_intent(text: str) -> Optional[str]:
    t = _normalize_text(text)
    # Numérico directo
    if re.fullmatch(r"[1-5]", t):
        return t
    # Atajos globales
    if t in INTENTS["inicio"]["keys"]:
        return "9"
    if t in INTENTS["atras"]["keys"]:
        return "0"
    # Palabras clave
    for intent, cfg in INTENTS.items():
        if intent in ("inicio", "atras"):
            continue
        for k in cfg["keys"]:
            if k in t:
                return intent
    return None

# --------------------------------- Motor --------------------------------------

class FlowEngine:
    """
    Motor simple basado en nodos/links definidos en flow.json.

    Estructura esperada en flow.json (recomendada):
    {
      "menu_principal": {
        "reply": ["línea1", "línea2", "..."],
        "routes": { "1": "servicios", "2": "agendar", "3": "reagendar", "4": "consultar", "5": "hablar" }
      },
      "servicios": { "reply": ["..."], "routes": {"9": "menu_principal", "0": "menu_principal"} },
      ...
    }

    - 'reply'  : lista de líneas a devolver
    - 'routes' : ruteo por opción/atajo → id de siguiente nodo
    - ids      : se recomienda usar minúsculas (snake_case)
    """

    def __init__(self, flow_path: Optional[str] = None) -> None:
        self.flow_path = flow_path or os.getenv("FLOW_JSON_PATH", "flow.json")
        self.nodes: Dict[str, Dict[str, Any]] = {}
        self.edges: List[Dict[str, Any]] = []   # mantenido por compatibilidad de logs
        self.start: str = "menu_principal"      # nodo de inicio por defecto
        self._load()

    # ---------------------------- Carga del flujo -----------------------------

    def _load(self) -> None:
        with open(self.flow_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        # Soportar dos formatos: {"nodes": {...}, "edges":[...]} o {...}
        if isinstance(data, dict) and "nodes" in data:
            raw_nodes = data.get("nodes") or {}
            self.edges = data.get("edges") or []
            self.start = (data.get("start") or "menu_principal").strip().lower()
        else:
            raw_nodes = data
            self.edges = []
            self.start = "menu_principal"

        # Normalizar ids a minúsculas y asegurar reply como lista
        norm_nodes: Dict[str, Dict[str, Any]] = {}
        for k, v in (raw_nodes or {}).items():
            node_id = (k or "").strip().lower() or "menu_principal"
            node = dict(v or {})
            reply = node.get("reply")
            if reply is None:
                # Permitir 'label' → 'reply'
                reply = node.get("label")
            if isinstance(reply, str):
                reply = [reply]
            if not isinstance(reply, list):
                reply = [""]

            # normalizar routes (si vienen como lista de edges, las ignoramos aquí)
            routes = node.get("routes") or {}
            # Asegurar que siempre existan atajos globales
            routes.setdefault("9", "menu_principal")
            routes.setdefault("0", "menu_principal")

            norm_nodes[node_id] = {
                "id": node_id,
                "reply": reply,
                "routes": routes,
            }

        self.nodes = norm_nodes

        default_menu = {
            "id": "menu_principal",
            "reply": ["Bienvenido. (configura 'menu_principal' en flow.json)"],
            "routes": {"9": "menu_principal", "0": "menu_principal"},
        }

        if "menu_principal" not in self.nodes:
            log.warning("FLOW sin 'menu_principal': se inyecta nodo de inicio por defecto")
            self.nodes["menu_principal"] = dict(default_menu)

        if self.start not in self.nodes:
            self.start = "menu_principal"

        self.nodes.setdefault("menu_principal", dict(default_menu))

        log.info("FLOW PATH=%s", self.flow_path)
        log.info("FLOW NODES=%d START=%s", len(self.nodes), self.start)
    # ------------------------------- Ejecución --------------------------------

    def run(self, text: str, current_id: Optional[str]) -> Dict[str, Any]:
        """
        Ejecuta un paso del flujo y devuelve:
        {"reply": [..], "next": "<node_id>"}

        - Respeta atajos globales: '9' (inicio) y '0' (atrás) → menu_principal
        - Si el nodo actual no existe o es vacío, cae a 'start'
        - Rutea por:
            1) opción numérica 1..5
            2) intención inferida por palabras
            3) fallback al mismo nodo (eco de reply)
        """
        # Normalizar nodo actual
        curr = (current_id or "").strip().lower()
        if not curr or curr not in self.nodes:
            curr = self.start

        # Atajos universales
        tnorm = _normalize_text(text)
        if tnorm in INTENTS["inicio"]["keys"] or tnorm == "9":
            node = self.nodes[self.start]
            return {"reply": node["reply"], "next": node["id"]}

        if tnorm in INTENTS["atras"]["keys"] or tnorm == "0":
            node = self.nodes[self.start]
            return {"reply": node["reply"], "next": node["id"]}

        # Nodo actual
        node = self.nodes.get(curr, self.nodes[self.start])
        routes: Dict[str, str] = node.get("routes") or {}

        # 1) Si el usuario manda un número que exista en routes → transiciona
        if re.fullmatch(r"\d+", tnorm) and tnorm in routes:
            next_id = routes[tnorm].strip().lower()
            next_node = self.nodes.get(next_id, self.nodes[self.start])
            return {"reply": next_node["reply"], "next": next_node["id"]}

        # 2) Intent por texto libre (mapear a opción si existe)
        intent = _infer_intent(tnorm) or ""
        mapping = {
            "servicios": "1",
            "agendar": "2",
            "reagendar": "3",
            "cancelar": "3",   # dentro de la sección 3 puedes ofrecer subopción “cancelar”
            "consultar": "4",
            "hablar": "5",
        }
        if intent in ("1", "2", "3", "4", "5"):
            opt = intent
        elif intent in mapping:
            opt = mapping[intent]
        else:
            opt = ""

        if opt and opt in routes:
            next_id = routes[opt].strip().lower()
            next_node = self.nodes.get(next_id, self.nodes[self.start])
            return {"reply": next_node["reply"], "next": next_node["id"]}

        # 3) Fallback: repetir el nodo actual (útil para “elige una opción 1–5”)
        return {"reply": node["reply"], "next": node["id"]}
