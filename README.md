# AnaBot Webhooks

Servicio FastAPI listo para Railway que expone webhooks de Telegram y WhatsApp Cloud API.

## Requisitos

- Python 3.11+
- Variables de entorno (ver `.env.example`).

## Uso local

```
powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env  # ajusta tokens reales
uvicorn main:app --reload
```

Pruebas rapidas:

- GET http://127.0.0.1:8000/    -> texto "Servidor AnaBot activo".
- GET http://127.0.0.1:8000/health -> { "ok": true }.
- POST http://127.0.0.1:8000/webhook/telegram con un update valido.
- GET http://127.0.0.1:8000/webhook/whatsapp?hub.mode=subscribe&hub.verify_token=verify_me&hub.challenge=123.

## Deployment en Railway

1. Configura el repositorio y las variables del servicio web:
   - TELEGRAM_BOT_TOKEN
   - TELEGRAM_SECRET_TOKEN (opcional)
   - WHATSAPP_TOKEN
   - WHATSAPP_PHONE_NUMBER_ID
   - WHATSAPP_VERIFY_TOKEN
2. Railway usa el Procfile (`web: uvicorn main:app --host 0.0.0.0 --port ${PORT:-8080}`).
3. Verifica las rutas /health, /webhook/telegram y /webhook/whatsapp tras el deploy.
