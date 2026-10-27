"""Production configuration validation tests."""
import pytest

from app.core.config import Settings, validate_runtime_settings


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
