# AnaBot - Asistente Virtual Médico

AnaBot es un asistente para agendar pacientes vía FastAPI, con integraciones a Telegram y Google Calendar. El proyecto está listo para correr en local (SQLite por defecto) y en Railway (PostgreSQL gestionado).

---

## Requisitos

- Python 3.11+
- pip actualizado (`python -m pip install --upgrade pip`)
- Cuenta Telegram Bot (BotFather) si usarás webhooks
- Credenciales de Google Calendar si quieres crear eventos
- Railway (opcional) para desplegar

---

## Configuración Local (Windows / PowerShell)

````powershell
# 1. Crear entorno virtual
python -m venv .venv

# 2. Activar
.\.venv\Scripts\Activate.ps1

# 3. Actualizar pip e instalar dependencias
python -m pip install --upgrade pip
pip install -r requirements.txt

# 4. Variables de entorno (opcional)
copy .env.example .env  # deja DATABASE_URL=sqlite:///./dev.db

# 5. Inicializar base de datos
python init_db.py

# 6. Levantar el servidor
uvicorn main2:app --reload

# 7. Probar healthcheck de BD
curl http://127.0.0.1:8000/db/ping
# → {"db":"ok","val":1}
````

Si `uvicorn` no está en PATH, usa `python -m uvicorn main2:app --reload`.

---

## Configuración en Railway (Producción)

1. Configura variables en el servicio web:
   - `DATABASE_URL=postgresql+psycopg://postgres:<password>@postgres.railway.internal:5432/railway`
   - (Opcional) `GOOGLE_CALENDAR_TOKEN_JSON`, `TELEGRAM_TOKEN`, `PUBLIC_BASE_URL`, etc.
2. El Procfile ya expone `main2:app` mediante Uvicorn.
3. Tras el deploy o restart, verifica:
   - `https://<tu-app>.railway.app/db/ping` → `{ "db": "ok", "val": 1 }`
   - Revisa logs de arranque para ver líneas `ROUTE ...`.

---

## Endpoints Básicos

- `GET /health` → `{ "ok": true }`
- `GET /db/ping` → Ejecuta `SELECT 1` contra la base configurada.
- `POST /telegram/webhook` → Webhook de Telegram (requiere token).

---

## Notas sobre calendarios

- `utils/google_calendar.py` busca credenciales en `GOOGLE_CALENDAR_TOKEN_JSON` o `token.json`.
- Usa el scope `https://www.googleapis.com/auth/calendar`.
- `create_calendar_event` devuelve `id` y `htmlLink`, almacenados en la tabla `appointments`.

---

## Estructura principal

- `config.py` → carga de settings (dotenv, fallback SQLite).
- `db.py` → engine SQLAlchemy (SQLite o Postgres).
- `models.py`, `repo.py` → ORM y capa de datos.
- `main2.py` → FastAPI + lógica conversacional.
- `init_db.py` → creación de tablas.
- `utils/google_calendar.py` → helper Google Calendar.

### Comandos útiles

```bash
git add Procfile requirements.txt .env.example config.py db.py init_db.py main2.py README.md
git commit -m "feat: fallback SQLite + Postgres con psycopg3; /db/ping; Procfile main2"
git push
```
