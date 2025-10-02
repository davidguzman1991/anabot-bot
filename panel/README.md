
# AnaBot – Panel Operativo

Panel de control para AnaBot, listo para Railway y Nixpacks.

## Requisitos
- Python 3.11+
- Acceso a la base de datos (`DATABASE_URL` o variables `PG*`).
- `requirements.txt` en la carpeta `panel/`.

## Instalación local
```powershell
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
```

## Ejecución local
```powershell
streamlit run dashboard.py
```

## Despliegue en Railway con Nixpacks
1. Sube la carpeta `panel/` como servicio en Railway.
2. Configura las variables de entorno necesarias para la base de datos (`DATABASE_URL` o `PG*`).
3. Usa este Start Command:
	```
	streamlit run dashboard.py --server.port $PORT --server.address 0.0.0.0
	```

## Notas
- El panel es solo backoffice y no envía mensajes a usuarios.
- Requiere conexión a la misma base de datos que el bot.
