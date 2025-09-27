import os
from sqlalchemy import create_engine
from sqlalchemy.engine.url import make_url
from sqlalchemy.orm import sessionmaker

RAW_URL = os.getenv("DATABASE_URL", "sqlite:///./dev.db")


def normalize_url(raw: str):
    url = make_url(raw)

    if url.drivername.startswith("postgresql") and "+psycopg" not in url.drivername:
        url = url.set(drivername="postgresql+psycopg")

    host = (url.host or "").lower()
    q = dict(url.query) if url.query else {}

    if "proxy.rlwy.net" in host or host.endswith(".railway.app"):
        q.setdefault("sslmode", "require")
        url = url.set(query=q)
    else:
        if "sslmode" in q:
            q.pop("sslmode", None)
            url = url.set(query=q)

    return url


URL = normalize_url(RAW_URL)
engine = create_engine(URL, pool_pre_ping=True, future=True)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
