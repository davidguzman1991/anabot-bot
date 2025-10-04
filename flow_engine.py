# flow_engine.py
import json
import datetime
import re
from typing import Any, Dict, List, Optional

from hooks import Hooks

NAV_HINT_TEXT = "Escribe 1 para volver atrás o 9 para ir al inicio."


class MemoryStore:
    def __init__(self):
        self.sessions: Dict[str, Dict[str, Any]] = {}

    def get(self, sid: str) -> Dict[str, Any]:
        now_iso = datetime.datetime.utcnow().isoformat()
        if sid not in self.sessions:
            self.sessions[sid] = {
                "node": "MENU_PRINCIPAL",
                "ctx": {},
                "history": [],
                "_needs_on_enter": True,
                "last_activity": now_iso,
                "inactivity_stage": 0,
            }
        else:
            sess = self.sessions[sid]
            sess.setdefault("ctx", {})
            sess.setdefault("history", [])
            sess.setdefault("last_activity", now_iso)
            sess.setdefault("inactivity_stage", 0)
        return self.sessions[sid]

    def set(self, sid: str, data: Dict[str, Any]):
        self.sessions[sid] = data


class FlowEngine:
    def __init__(self, flow_path: str = "flow.json", store: Optional[MemoryStore] = None):
        with open(flow_path, "r", encoding="utf-8") as f:
            self.flow = json.load(f)
        self.start = self.flow.get("start", "MENU_PRINCIPAL")
        self.nodes = {n["id"]: n for n in self.flow.get("nodes", [])}
        self.globals = self.flow.get("globals", {})
        self.validations = self.globals.get("validations", {})
        self.messages = self.globals.get("messages", {})
        self.commands = {k: str(v) for k, v in self.globals.get("commands", {}).items()}
        base_shortcuts = {"to_human": "0", "back": "1", "home": "9"}
        custom_shortcuts = self.globals.get("shortcuts", {})
        self.shortcuts = {**base_shortcuts, **custom_shortcuts}
        self.hooks = Hooks(self.globals)

        import json
        from pathlib import Path
        from typing import Dict, Any, List, Optional

        class FlowEngine:
            def __init__(self, flow_path: str = "flow.json") -> None:
                self.flow_path = flow_path
                self.flow: Dict[str, Any] = {}
                self.nodes: Dict[str, Dict[str, Any]] = {}
                self.start_node: Optional[str] = None
                self._load()

            def _load(self) -> None:
                p = Path(self.flow_path)
                if not p.exists():
                    # Flow mínimo por si falta el archivo
                    self.flow = {"nodes": [], "edges": [], "defaultStartNode": None}
                    self.nodes = {}
                    self.start_node = None
                    return
                self.flow = json.loads(p.read_text(encoding="utf-8"))

                # aceptar tanto {"id","label"} como {"data":{"label":..}} y edges con "target"/"to"
                nodes = self.flow.get("nodes", [])
                self.nodes = {}
                for n in nodes:
                    nid = n.get("id") or (n.get("data") or {}).get("id")
                    if not nid:
                        continue
                    label = n.get("label") or (n.get("data") or {}).get("label") or ""
                    payload = n.get("payload") or (n.get("data") or {}).get("payload") or {}
                    self.nodes[nid] = {"id": nid, "label": label, "payload": payload}

                # start node: defaultStartNode o primer nodo
                self.start_node = (
                    self.flow.get("defaultStartNode")
                    or self.flow.get("startNode")
                    or (nodes[0]["id"] if nodes else None)
                )

                # indexar edges en forma normalizada
                self.edges: List[Dict[str, Any]] = []
                for e in self.flow.get("edges", []):
                    src = e.get("source") or e.get("from")
                    dst = e.get("target") or e.get("to")
                    cond = e.get("cond") or e.get("condition") or e.get("label") or ""
                    if src and dst:
                        self.edges.append({"source": src, "target": dst, "cond": cond.lower().strip()})

            def _next_by_text(self, current_id: str, text: str) -> Optional[str]:
                t = (text or "").lower().strip()
                # match exacto por cond o por número (1,2,3) si cond empieza con "1) ..."
                for e in self.edges:
                    if e["source"] != current_id:
                        continue
                    c = e["cond"]
                    if not c:
                        continue
                    if t == c:
                        return e["target"]
                    # soporte numerado "1", "2", etc.
                    if c[:2].isdigit() and t == c[:1]:
                        return e["target"]
                return None

            def run(self, text: str, current_id: Optional[str]) -> Dict[str, Any]:
                """
                Devuelve {"reply": [msg,...], "next": node_id} o {} si no hay flujo.
                """
                if not self.nodes or not self.start_node:
                    return {}
                node_id = current_id or self.start_node
                nxt = self._next_by_text(node_id, text)
                if nxt is None:
                    # si no hay transición, devuelve el menú del nodo actual
                    node = self.nodes.get(node_id)
                    if not node:
                        return {}
                    label = node.get("label") or ""
                    payload = node.get("payload") or {}
                    menu = payload.get("menu") or []
                    parts = [label] + [f"{i+1}) {opt}" for i, opt in enumerate(menu)]
                    return {"reply": ["\n".join([p for p in parts if p])], "next": node_id}
                node = self.nodes.get(nxt)
                if not node:
                    return {}
                label = node.get("label") or ""
                payload = node.get("payload") or {}
                menu = payload.get("menu") or []
                parts = [label] + [f"{i+1}) {opt}" for i, opt in enumerate(menu)]
                return {"reply": ["\n".join([p for p in parts if p])], "next": nxt}
        st["_needs_on_enter"] = True
        st["inactivity_stage"] = 0

    def _validate(self, pattern_key: Optional[str], text: str) -> tuple[bool, Optional[str]]:
        if not pattern_key:
            return True, None
        rule = self.validations.get(pattern_key)
        if not rule:
            return True, None
        pattern = rule if isinstance(rule, str) else rule.get("regex")
        error = None
        if isinstance(rule, dict):
            error = rule.get("error")
        if not pattern:
            return True, error
        return bool(re.match(pattern, text.strip())), error

    def _append_nav_hint(self, node: Dict[str, Any], message: str) -> str:
        if node.get("id") == self.start:
            return message
        if node.get("hide_navigation"):
            return message
        if NAV_HINT_TEXT in message:
            return message
        if message.endswith("\n"):
            return f"{message}{NAV_HINT_TEXT}"
        return f"{message}\n\n{NAV_HINT_TEXT}"

    # ------------------------------------------------------------------

    def process(self, session_id: str, text: str) -> Dict[str, Any]:
        st = self.store.get(session_id)
        ctx = st.setdefault("ctx", {})
        st["last_activity"] = datetime.datetime.utcnow().isoformat()
        st["inactivity_stage"] = 0

        user_text = (text or "").strip()

        node_id = st.get("node", self.start)
        node = self.nodes.get(node_id) or self.nodes.get(self.start)
        if not node:
            return {"message": "Flujo no encontrado.", "node": node_id or "?"}

        next_override = None
        if st.get("_needs_on_enter", True):
            next_override = self._run_hooks_list(node.get("on_enter_hooks"), ctx)
            if next_override is None and self._normalize_type(node) == "choice":
                next_override = self._run_hooks_list(node.get("hooks"), ctx)
            if next_override:
                self._set_node(st, next_override, push_history=True)
                self.store.set(session_id, st)
                return self.process(session_id, user_text)
            st["_needs_on_enter"] = False
            self.store.set(session_id, st)
            node = self.nodes.get(st["node"])

        ntype = self._normalize_type(node)

        if not user_text:
            self.store.set(session_id, st)
            return self._out(session_id)

        if ntype == "choice":
            opts: Dict[str, Dict[str, Any]] = {}
            for opt in node.get("options", []):
                opts[str(opt["key"])]=opt

            dyn_key = node.get("dynamic_options_from")
            if dyn_key:
                dyn_items = ctx.get(dyn_key, [])
                for idx, item in enumerate(dyn_items, start=1):
                    if isinstance(item, dict):
                        key = str(item.get("key") if item.get("key") is not None else idx)
                        label = item.get("label") or str(item.get("value") or idx)
                        value = item.get("value") if "value" in item else item.get("label")
                        next_id = item.get("next") if item.get("next") else node.get("on_select_next")
                    else:
                        key = str(idx)
                        label = str(item)
                        value = item
                        next_id = node.get("on_select_next")
                    opts[key] = {"key": key, "label": label, "value": value, "next": next_id}

            post_opts = {str(p["key"]): p for p in node.get("post_options", [])}

            if user_text in opts:
                chosen = opts[user_text]
                self._apply_save_map(chosen.get("save"), ctx)
                value = chosen.get("value") if "value" in chosen else chosen.get("label")
                if node.get("on_select") and node["on_select"].get("save_as"):
                    self._set_nested(ctx, node["on_select"]["save_as"], value)
                if chosen.get("on_select"):
                    hook_next = self._run_hooks_list([chosen["on_select"]], ctx, user_text)
                    if hook_next:
                        self._set_node(st, hook_next)
                        self.store.set(session_id, st)
                        return self._out(session_id)
                if chosen.get("hooks"):
                    hook_next = self._run_hooks_list(chosen.get("hooks"), ctx, user_text)
                    if hook_next:
                        self._set_node(st, hook_next)
                        self.store.set(session_id, st)
                        return self._out(session_id)
                next_id = chosen.get("next") or node.get("next")
                if not next_id and user_text in post_opts:
                    next_id = post_opts[user_text].get("next")
                if not next_id:
                    next_id = self.start
                self._set_node(st, next_id)
                self.store.set(session_id, st)
                return self._out(session_id)

            handled = self._handle_commands(user_text, session_id, st, options=opts)
            if handled:
                return handled

            if user_text in post_opts:
                chosen = post_opts[user_text]
                self._apply_save_map(chosen.get("save"), ctx)
                next_id = chosen.get("next") or self.start
                self._set_node(st, next_id)
                self.store.set(session_id, st)
                return self._out(session_id)

            fb = node.get("fallback", {})
            base_msg = fb.get("message", self.messages.get("invalid_option", "Opción inválida."))
            prompt = node.get("text") or node.get("message") or ""
            message = base_msg if not prompt else f"{base_msg}\n\n{prompt}"
            message = self._append_nav_hint(node, message)
            return {"message": message, "node": node_id, "options": self._options(node, ctx)}

        handled = self._handle_commands(user_text, session_id, st)
        if handled:
            return handled

        if ntype == "input":
            ok, err_msg = self._validate(node.get("validation") or node.get("validate"), user_text)
            if not ok:
                prompt = node.get("text") or ""
                message = err_msg or self.messages.get("invalid_field", "Dato inválido.")
                if prompt:
                    message = f"{message}\n\n{prompt}"
                message = self._append_nav_hint(node, message)
                return {"message": message, "node": node_id}
            if node.get("save_as"):
                self._set_nested(ctx, node["save_as"], user_text.strip())
            if node.get("save"):
                self._set_nested(ctx, node["save"], user_text.strip())
            hook_next = self._run_hooks_list(node.get("hooks"), ctx, user_text)
            if hook_next:
                self._set_node(st, hook_next)
                self.store.set(session_id, st)
                return self._out(session_id)
            next_id = node.get("next") or self.start
            self._set_node(st, next_id)
            self.store.set(session_id, st)
            return self._out(session_id)

        if ntype == "message":
            hook_next = self._run_hooks_list(node.get("hooks"), ctx)
            next_id = hook_next or node.get("next") or self.start
            self._set_node(st, next_id)
            self.store.set(session_id, st)
            return self._out(session_id)

        return {"message": "Nodo no soportado.", "node": node_id}

    def _handle_commands(self, user_text: str, session_id: str, st: Dict[str, Any], options: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        if not user_text:
            return None
        ctx = st["ctx"]
        home_code = self.commands.get("home") or self.shortcuts.get("home")
        if home_code and user_text == str(home_code):
            st["history"] = []
            self._set_node(st, self.start, push_history=False)
            self.store.set(session_id, st)
            return self._out(session_id)
        back_code = self.commands.get("back") or self.shortcuts.get("back")
        if back_code and user_text == str(back_code):
            if options and user_text in options:
                return None
            history = st.get("history", [])
            if history:
                previous = history.pop()
                st["node"] = previous
                st["_needs_on_enter"] = True
                st["inactivity_stage"] = 0
                self.store.set(session_id, st)
                return self._out(session_id)
            self._set_node(st, self.start, push_history=False)
            self.store.set(session_id, st)
            return self._out(session_id)
        human_code = self.shortcuts.get("to_human")
        if human_code and user_text == str(human_code):
            if "CONTACTO" in self.nodes:
                self._set_node(st, "CONTACTO")
                self.store.set(session_id, st)
                return self._out(session_id)
            self.hooks.call("handoff.to_human", ctx=ctx)
            message = self.messages.get("handoff", "Te transfiero con un humano.")
            return {"message": message, "node": st.get("node", self.start)}
        return None

    def _render_message(self, msg: str, ctx: Dict[str, Any], node: Dict[str, Any]) -> str:
        consent = self.messages.get("consent", "")
        saludo = "día"
        hour = datetime.datetime.now().hour
        if 12 <= hour < 19:
            saludo = "tarde"
        elif hour >= 19 or hour < 6:
            saludo = "noche"
        base = (msg or "").replace("{saludo}", saludo).replace("@consent", consent)
        return self._append_nav_hint(node, base)

    def _options(self, node: Dict[str, Any], ctx: Dict[str, Any]) -> List[str]:
        opts: List[str] = []
        for opt in node.get("options", []):
            opts.append(f"{opt['key']}) {opt['label']}")
        for opt in node.get("post_options", []):
            opts.append(f"{opt['key']}) {opt['label']}")
        dyn_key = node.get("dynamic_options_from")
        if dyn_key:
            dyn_list = ctx.get(dyn_key, [])
            for idx, item in enumerate(dyn_list, start=1):
                if isinstance(item, dict):
                    key = item.get("key") if item.get("key") is not None else idx
                    label = item.get("label") or str(item.get("value") or idx)
                else:
                    key = idx
                    label = str(item)
                opts.append(f"{key}) {label}")
        return opts

    def _out(self, session_id: str) -> Dict[str, Any]:
        st = self.store.get(session_id)
        node = self.nodes.get(st.get("node", self.start), {})
        message = node.get("message")
        if message is None:
            message = node.get("text", "")
        msg = self._render_message(message, st.get("ctx", {}), node)
        return {"message": msg, "node": node.get("id"), "options": self._options(node, st.get("ctx", {}))}
