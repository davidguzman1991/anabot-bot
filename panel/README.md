# AnaBot – Panel Operativo

## Requisitos
- Python 3.11+
- Acceso a la misma base de datos utilizada por el bot (`DATABASE_URL` o variables `PG*`).

## Instalación
```bash
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
```

## Ejecución local
```bash
streamlit run dashboard.py
```

El panel requiere las variables de conexión a Postgres (`PGUSER`, `PGPASSWORD`, `PGHOST`, `PGPORT`, `PGDATABASE`).

## Secciones disponibles
- Conversaciones
- Detalle de conversación
- Citas
- Métricas
- Gestión

El panel es de solo backoffice y no envía mensajes a usuarios.
