from dataclasses import replace

import pytest
from cryptography.fernet import Fernet

from workout_relay.config import Settings
from workout_relay.database import Database


def test_render_external_url_supplies_production_base_url(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("RENDER_EXTERNAL_URL", "https://workout-relay.onrender.com")
    monkeypatch.delenv("BASE_URL", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://example/test")
    monkeypatch.setenv("COOKIE_SECURE", "true")
    monkeypatch.setenv("GARMIN_MODE", "live")
    monkeypatch.setenv("MASTER_ENCRYPTION_KEY", Fernet.generate_key().decode())
    assert Settings.from_env().base_url == "https://workout-relay.onrender.com"


def test_retention_and_size_limits_must_be_positive(settings):
    with pytest.raises(ValueError, match="PLAN_RETENTION_DAYS"):
        replace(settings, plan_retention_days=0).validate()
    with pytest.raises(ValueError, match="MAX_PLAN_BYTES"):
        replace(settings, max_plan_bytes=0).validate()


@pytest.mark.parametrize(
    "url", ["postgresql://user:pw@db.internal/app", "postgres://user:pw@db.internal/app"]
)
def test_platform_postgres_urls_use_installed_psycopg_driver(monkeypatch, url):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("MASTER_ENCRYPTION_KEY", Fernet.generate_key().decode())
    settings = Settings.from_env()
    assert settings.database_url == "postgresql+psycopg://user:pw@db.internal/app"
    # Building the engine imports the driver without connecting.
    Database(settings.database_url)


@pytest.mark.parametrize(
    "host",
    [
        "ep-soft-breeze-b2pvm67q-pooler.c-6.eu-central-1.aws.neon.tech",
        "pgbouncer.internal:6432",
    ],
)
def test_pooled_connection_strings_are_refused(settings, host):
    """A pooler breaks session advisory locks quietly, so fail loudly instead."""
    with pytest.raises(ValueError, match="transaction pooler"):
        replace(settings, database_url=f"postgresql+psycopg://user:pw@{host}/relay").validate()


def test_direct_connection_strings_are_accepted(settings):
    direct = "postgresql+psycopg://user:pw@ep-soft-breeze-b2pvm67q.c-6.eu-central-1.aws.neon.tech/relay"
    replace(settings, database_url=direct).validate()
