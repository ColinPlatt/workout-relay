"""Application configuration with safe production defaults."""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path

from cryptography.fernet import Fernet
from dotenv import load_dotenv


def _bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    return default if value is None else value.lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    app_env: str
    base_url: str
    database_url: str
    cookie_secure: bool
    garmin_mode: str
    master_encryption_key: str
    session_days: int = 30
    plan_retention_days: int = 90
    max_plan_bytes: int = 1_048_576

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv()
        app_env = os.getenv("APP_ENV", "development").lower()
        database_url = os.getenv("DATABASE_URL", "sqlite:///./data/workout_relay.db")
        key = os.getenv("MASTER_ENCRYPTION_KEY") or ""
        if not key and app_env != "production":
            key = _development_key(database_url)
        settings = cls(
            app_env=app_env,
            base_url=os.getenv("BASE_URL", "http://localhost:8000").rstrip("/"),
            database_url=database_url,
            cookie_secure=_bool("COOKIE_SECURE", app_env == "production"),
            garmin_mode=os.getenv("GARMIN_MODE", "mock").lower(),
            master_encryption_key=key,
            session_days=int(os.getenv("SESSION_DAYS", "30")),
            plan_retention_days=int(os.getenv("PLAN_RETENTION_DAYS", "90")),
            max_plan_bytes=int(os.getenv("MAX_PLAN_BYTES", "1048576")),
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        if self.app_env not in {"development", "test", "production"}:
            raise ValueError("APP_ENV must be development, test, or production")
        if self.garmin_mode not in {"mock", "live"}:
            raise ValueError("GARMIN_MODE must be mock or live")
        if not self.master_encryption_key:
            raise ValueError("MASTER_ENCRYPTION_KEY is required")
        try:
            Fernet(self.master_encryption_key.encode())
        except Exception as exc:
            raise ValueError("MASTER_ENCRYPTION_KEY must be a valid Fernet key") from exc
        if self.app_env == "production":
            if not self.cookie_secure:
                raise ValueError("COOKIE_SECURE must be true in production")
            if not self.base_url.startswith("https://"):
                raise ValueError("BASE_URL must use HTTPS in production")
            if self.garmin_mode != "live":
                raise ValueError("GARMIN_MODE must be live in production")


def _development_key(database_url: str) -> str:
    """Persist a development key next to local SQLite data.

    Production never generates encryption keys automatically.
    """
    if database_url.startswith("sqlite:///"):
        db_path = Path(database_url.removeprefix("sqlite:///"))
        key_path = db_path.parent / ".development-master.key"
    else:
        key_path = Path("./data/.development-master.key")
    key_path.parent.mkdir(parents=True, exist_ok=True)
    if not key_path.exists():
        key_path.write_text(Fernet.generate_key().decode())
        key_path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    return key_path.read_text().strip()
