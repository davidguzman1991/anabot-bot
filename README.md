# AnaBot – Servicio Bot

Servicio listo para Railway usando Nixpacks. Expone webhooks de Telegram y WhatsApp Cloud API.

## Requisitos
- Python 3.11+
- Variables de entorno (ver `.env.example`).
- `requirements.txt` en la raíz.

## Instalación y uso local
```bash
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env  # Ajusta tokens reales
uvicorn main:app --reload  # Si usas FastAPI
python main.py            # Si usas solo polling
```

## Despliegue en Railway con Nixpacks
1. Sube el repo a Railway.
2. Configura las variables de entorno necesarias:
   - `DATABASE_URL` (conexión a Postgres)
   - `TELEGRAM_BOT_TOKEN`, `TELEGRAM_SECRET_TOKEN` (opcional)
   - `WHATSAPP_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_VERIFY_TOKEN`
3. Railway detecta automáticamente el entorno con Nixpacks.
4. Usa uno de estos Start Command según el modo:
   - **API (webhook):**
     ```
     uvicorn main:app --host 0.0.0.0 --port $PORT
     ```
   - **Solo polling:**
     ```
     python main.py
     ```

## Endpoints expuestos
- `GET /health`
- `POST /webhook/whatsapp`
- `POST /webhook/telegram`

## Notas
- El servicio usa la variable `DATABASE_URL` para conectarse a la base de datos.
- Los webhooks responden 200 inmediatamente; los envíos a Telegram/WhatsApp se hacen de forma asíncrona y los errores se registran en consola.
