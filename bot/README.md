# AnaBot – Servicio Bot

## Requisitos
- Python 3.11+
- Variables de entorno (ver `../.env.example`).

## Instalación
```bash
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
```

## Ejecución local
```bash
uvicorn main:app --host 0.0.0.0 --port 8080 --reload
```

El servicio expone:
- `GET /health`
- `POST /webhook/whatsapp`
- `POST /webhook/telegram`

## Variables de entorno clave
- `DATABASE_URL`
- `TELEGRAM_BOT_TOKEN`
- `WHATSAPP_TOKEN`
- `WHATSAPP_PHONE_NUMBER_ID`
- `WHATSAPP_VERIFY_TOKEN`
- `ANA_VERIFY`, etc.

Las mismas variables se utilizan en despliegue para Railway.
