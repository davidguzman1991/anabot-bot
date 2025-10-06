import logging
from fastapi import FastAPI
from flow_engine_safe import FlowEngine

app = FastAPI()
engine = None

def init_flow():
    global engine
    try:
        engine = FlowEngine("flow.json")
    except Exception:
        logging.exception("Fallo al inicializar FlowEngine")
        from flow_engine_safe import FlowEngine as FE
        engine = FE("flow.json")

init_flow()

@app.get("/health/flow")
def health_flow():
    return {
        "ok": True,
        "start_state": engine.start_state() if engine else None,
        "states_count": len(engine.data.get("states", {})) if engine else 0
    }

