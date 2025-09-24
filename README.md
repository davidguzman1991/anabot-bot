# AnaBot – Asistente Virtual Médico

AnaBot es un chatbot especializado en la gestión de pacientes del **Dr. Guzmán**, con integración a **WhatsApp**, **Telegram** y **Google Calendar**.  
Permite agendar consultas, enviar recordatorios automáticos y responder dudas frecuentes de los pacientes con un tono empático y humanizado.

---

## 🚀 Requisitos previos

- **Python 3.10+**
- Cuenta de **Meta for Developers** con un número de WhatsApp Business
- Cuenta de **Telegram Bot** creada con [BotFather](https://t.me/botfather)
- Proyecto en **Railway** o entorno con soporte para `uvicorn`

---

## 📂 Archivos principales

- `main.py` → Código principal del bot (FastAPI)
- `requirements.txt` → Dependencias
- `runtime.txt` → Versión de Python
- `Procfile` → Indica a Railway cómo correr la app
- `chatbot_ana_base_conocimiento.csv` → Base de conocimiento de respuestas
- `README.md` → Este archivo 🙂

---

## ⚙️ Variables de entorno necesarias

En **Railway** o en un archivo `.env` local define:

```env
# WhatsApp
WHATSAPP_TOKEN=tu_token_largo_de_meta
WHATSAPP_PHONE_ID=tu_phone_number_id
ANA_VERIFY=ANA_CHATBOT

# Telegram
TELEGRAM_BOT_TOKEN=tu_token_de_botfather
TELEGRAM_CHAT_ID=opcional_si_usas_solo_notificaciones

# Google Calendar
GOOGLE_CALENDAR_ID=primary
GOOGLE_TOKEN_JSON={"type":"authorized_user","client_id":"","client_secret":"","refresh_token":""}

# Configuración general
APPT_DURATION_MIN=45
PORT=8000
INTERNAL_CHAT_URL=http://127.0.0.1:$PORT/chat
