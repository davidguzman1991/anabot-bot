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
        self.store = store or MemoryStore()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _normalize_type(self, node: Dict[str, Any]) -> str:
        ntype = (node.get("type") or "").lower()
        if ntype == "menu":
            return "choice"
        return ntype or "message"

    def _get_nested(self, data: Dict[str, Any], parts: List[str]) -> Any:
        cur: Any = data
        for part in parts:
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            else:
                return None
        return cur

    def _set_nested(self, ctx: Dict[str, Any], path: str, value: Any):
        if not path:
            return
        parts = path.split(".")
        cur = ctx
        for part in parts[:-1]:
            if part not in cur or not isinstance(cur[part], dict):
                cur[part] = {}
            cur = cur[part]
        cur[parts[-1]] = value

    def _resolve_path(self, path: str, ctx: Dict[str, Any]) -> Any:
        if not path:
            return None
        parts = path.split(".")
        value = self._get_nested(ctx, parts)
        if value is not None:
            return value
        return self._get_nested(self.globals, parts)

    def _resolve_value(self, value: Any, ctx: Dict[str, Any], current_text: str = "") -> Any:
        if isinstance(value, str):
            if value in ("@input", "{input}"):
                return current_text
            if value.startswith("@"):
                return self._resolve_path(value[1:], ctx)
            if value.startswith("{") and value.endswith("}"):
                return self._resolve_path(value[1:-1], ctx)
        return value

    def _run_hook(self, hook_spec: Any, ctx: Dict[str, Any], current_text: str = "") -> Any:
        if not hook_spec:
            return None
        if isinstance(hook_spec, str):
            hook_spec = {"hook": hook_spec}
        name = hook_spec.get("hook")
        if not name:
            return None
        args = [self._resolve_value(arg, ctx, current_text) for arg in hook_spec.get("args", [])]
        result = self.hooks.call(name, *args, ctx=ctx)
        save_as = hook_spec.get("save_as")
        if save_as:
            self._set_nested(ctx, save_as, result)
        return result

    def _run_hooks_list(self, hook_specs: Optional[List[Dict[str, Any]]], ctx: Dict[str, Any], current_text: str = "") -> Optional[str]:
        if not hook_specs:
            return None
        for spec in hook_specs:
            result = self._run_hook(spec, ctx, current_text)
            if isinstance(result, bool):
                if result and spec.get("if_true_next"):
                    return spec["if_true_next"]
                if (not result) and spec.get("if_false_next"):
                    return spec["if_false_next"]
        return None

    def _apply_save_map(self, save_spec: Any, ctx: Dict[str, Any]):
        if isinstance(save_spec, dict):
            for path, value in save_spec.items():
                self._set_nested(ctx, path, value)

    def _set_node(self, st: Dict[str, Any], next_id: str, push_history: bool = True):
        if not next_id:
            return
        if push_history:
            history = st.setdefault("history", [])
            current = st.get("node")
            if current and (not history or history[-1] != current):
                history.append(current)
        st["node"] = next_id
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
