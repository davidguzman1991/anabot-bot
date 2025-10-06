# config.py
from __future__ import annotations
import os
from dotenv import load_dotenv

# Cargar .env local SI existe (en Railway usará Variables del panel)
load_dotenv()

# --------- WhatsApp / Telegram (opcional) ----------
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN", "")
WHATSAPP_PHONE_NUMBER_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")
WHATSAPP_VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN", "")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_SECRET_TOKEN = os.getenv("TELEGRAM_SECRET_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "")

# --------- Flujo ----------
FLOW_JSON_PATH = os.getenv("FLOW_JSON_PATH", "flow.json")
DURACION_CITA_MIN = int(os.getenv("DURACION_CITA_MIN", "45"))  # <— sin tildes, sin espacios

# --------- Base de datos ----------
# Opción A: usar DATABASE_URL directamente
DATABASE_URL = os.getenv("DATABASE_URL", "")

# Opción B: construir desde PG* si están presentes
PGUSER = os.getenv("PGUSER")
PGPASSWORD = os.getenv("PGPASSWORD")
PGHOST = os.getenv("PGHOST")
PGPORT = os.getenv("PGPORT")
PGDATABASE = os.getenv("PGDATABASE")

if all([PGUSER, PGPASSWORD, PGHOST, PGPORT, PGDATABASE]):
    DATABASE_URL = f"postgresql://{PGUSER}:{PGPASSWORD}@{PGHOST}:{PGPORT}/{PGDATABASE}"

# Seguridad básica: evitar prints de secretos
def redact(value: str) -> str:
    if not value:
        return ""
    return value[:4] + "..." if len(value) > 7 else "***"

def config_debug_snapshot() -> str:
    return (
        f"CFG: FLOW_JSON_PATH={FLOW_JSON_PATH} | "
        f"DURACION_CITA_MIN={DURACION_CITA_MIN} | "
        f"WHATSAPP_TOKEN={'OK' if WHATSAPP_TOKEN else 'MISSING'} | "
        f"PHONE_ID={'OK' if WHATSAPP_PHONE_NUMBER_ID else 'MISSING'} | "
        f"VERIFY_TOKEN={'OK' if WHATSAPP_VERIFY_TOKEN else 'MISSING'} | "
        f"DATABASE_URL={'OK' if DATABASE_URL else 'MISSING'}"
    )
