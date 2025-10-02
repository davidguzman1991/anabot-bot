import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

# Lee la URL de la base (Railway la inyecta como DATABASE_URL).
# Fallback a SQLite local para desarrollo.
DATABASE_URL = os.getenv('DATABASE_URL', 'sqlite:///./dev.db')

# SQLite necesita este connect_arg; Postgres no.
connect_args = {'check_same_thread': False} if DATABASE_URL.startswith('sqlite') else {}

# pool_pre_ping=True evita conexiones muertas en entornos cloud
engine = create_engine(DATABASE_URL, pool_pre_ping=True, connect_args=connect_args)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Dependencia para FastAPI: inyecta una sesión y la cierra al final
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
