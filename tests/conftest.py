from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from workout_relay.config import Settings


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        app_env="test",
        base_url="http://test",
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        cookie_secure=False,
        garmin_mode="mock",
        master_encryption_key=Fernet.generate_key().decode(),
        session_days=1,
        plan_retention_days=1,
        max_plan_bytes=200_000,
    )
