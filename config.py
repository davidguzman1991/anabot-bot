from __future__ import annotations

from functools import lru_cache

from dotenv import load_dotenv
from pydantic_settings import BaseSettings

load_dotenv()


class Settings(BaseSettings):
    DATABASE_URL: str = "sqlite:///./dev.db"
    GOOGLE_CALENDAR_TOKEN_JSON: str | None = None
    TELEGRAM_TOKEN: str | None = None
    PGUSER: str | None = None
    PGPASSWORD: str | None = None
    PGHOST: str | None = None
    PGPORT: str | None = None
    PGDATABASE: str | None = None

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "allow"  # Permite variables extra en el .env


@lru_cache
def get_settings() -> Settings:
    settings = Settings()

    if (
        settings.DATABASE_URL.startswith("sqlite")
        and settings.PGUSER
        and settings.PGPASSWORD
        and settings.PGHOST
        and settings.PGPORT
    ):
        database = settings.PGDATABASE or "postgres"
        pg_url = (
            f"postgresql://{settings.PGUSER}:{settings.PGPASSWORD}"
            f"@{settings.PGHOST}:{settings.PGPORT}/{database}"
        )
        object.__setattr__(settings, "DATABASE_URL", pg_url)

    return settings
