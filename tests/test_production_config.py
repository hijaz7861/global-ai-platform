"""Production configuration validation tests."""
import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings, settings, validate_runtime_settings
from app.main import app


def test_development_allows_local_sqlite_without_secret() -> None:
    validate_runtime_settings(
        Settings(environment="development", database_url="sqlite:///./test.db", secret_key=None)
    )


def test_production_rejects_missing_or_short_signing_key() -> None:
    config = Settings(
        environment="production",
        database_url="postgresql+psycopg://user:pass@localhost/db",
        secret_key="too-short",
    )
    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        validate_runtime_settings(config)


def test_production_rejects_sqlite_database() -> None:
    config = Settings(
        environment="production",
        database_url="sqlite:///./production.db",
        secret_key="x" * 40,
    )
    with pytest.raises(RuntimeError, match="PostgreSQL"):
        validate_runtime_settings(config)


def test_production_accepts_strong_key_and_postgresql() -> None:
    config = Settings(
        environment="production",
        database_url="postgresql+psycopg://user:pass@localhost/db",
        secret_key="x" * 40,
    )
    validate_runtime_settings(config)


def test_application_startup_fails_closed_without_production_key(monkeypatch) -> None:
    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr(settings, "secret_key", None)
    monkeypatch.setattr(
        settings, "database_url", "postgresql+psycopg://user:pass@localhost/db"
    )
    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        with TestClient(app):
            pass


def test_application_startup_fails_closed_for_production_sqlite(monkeypatch) -> None:
    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr(settings, "secret_key", "x" * 40)
    monkeypatch.setattr(settings, "database_url", "sqlite:///./production.db")
    with pytest.raises(RuntimeError, match="PostgreSQL"):
        with TestClient(app):
            pass
