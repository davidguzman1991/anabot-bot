import json, logging, re
from json import JSONDecodeError
from typing import Any, Dict

class FlowEngine:
    def __init__(self, path: str):
        self.path = path
        self.data: Dict[str, Any] = {}
        self._load()

    def _strip_comments(self, s: str) -> str:
        s = re.sub(r'/\*.*?\*/', '', s, flags=re.S)
        s = re.sub(r'//.*?$', '', s, flags=re.M)
        return s

    def _load(self) -> None:
        try:
            with open(self.path, 'r', encoding='utf-8') as f:
                raw = f.read().strip()
            cleaned = self._strip_comments(raw)
            decoder = json.JSONDecoder()
            obj, end = decoder.raw_decode(cleaned.lstrip())
            extra = cleaned[end:].strip()
            if extra:
                raise JSONDecodeError("Extra data after first JSON value", cleaned, end)
            # Normalize schema: allow either {"states":{...}} or flat {...}
            if "states" in obj and "start_state" in obj:
                self.data = obj
            else:
                self.data = {"version": "1.0", "start_state": "menu_principal", "states": obj}
            logging.info("FLOW loaded OK: %s", self.path)
        except JSONDecodeError as e:
            context = ''
            try:
                context = cleaned[max(0, e.pos-120): e.pos+120]
            except Exception:
                pass
            logging.error("FLOW JSON inválido en %s (pos=%s): %s\n...contexto...\n%s",
                          self.path, getattr(e, 'pos', '?'), e.msg, context)
            self._fallback("JSON inválido")
        except FileNotFoundError:
            logging.error("FLOW no encontrado: %s", self.path)
            self._fallback("Sin flow.json")
        except Exception as e:
            logging.exception("Error inesperado cargando flow.json: %s", e)
            self._fallback("Error inesperado")

    def _fallback(self, text: str) -> None:
        self.data = {
            "version": "1.0",
            "start_state": "menu_principal",
            "states": {
                "menu_principal": {
                    "type": "message",
                    "text": f"Modo seguro: {text}.",
                    "next": None
                }
            }
        }

    # Helpers to read current node safely
    def get_state(self, name: str) -> Dict[str, Any]:
        return self.data.get("states", {}).get(name, {})

    def start_state(self) -> str:
        return self.data.get("start_state", "menu_principal")
