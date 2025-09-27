import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.engine.url import make_url

RAW_URL = os.getenv("DATABASE_URL", "sqlite:///./dev.db")

def normalize_url(raw: str):
    url = make_url(raw)

    # Asegura psycopg v3 como driver
    if url.drivername.startswith("postgresql") and "+psycopg" not in url.drivername:
        url = url.set(drivername="postgresql+psycopg")

    host = (url.host or "").lower()
    q = dict(url.query) if url.query else {}

    # Proxy público → SSL obligatorio
    if "proxy.rlwy.net" in host or host.endswith(".railway.app"):
        q.setdefault("sslmode", "require")
        url = url.set(query=q)
    else:
        # Host interno (p.ej. postgres.railway.internal) → sin sslmode
        if "sslmode" in q:
            q.pop("sslmode", None)
            url = url.set(query=q)

    return url

URL = normalize_url(RAW_URL)
engine = create_engine(URL, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
