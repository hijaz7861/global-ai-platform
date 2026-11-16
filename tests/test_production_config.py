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


def _enabled_github_write_settings(**overrides):
    values = {
        # Use staging so this helper isolates the repository-write guard rather
        # than failing earlier at the independent production database guard.
        "environment": "staging",
        "database_url": "postgresql+psycopg://user:pass@localhost/db",
        "secret_key": "application-secret-key-at-least-32-chars",
        "ai_repository_writes_enabled": True,
        "github_app_id": "12345",
        "github_app_installation_id": "67890",
        "github_app_private_key_pem": (
            "-----BEGIN PRIVATE KEY-----\\nplaceholder\\n-----END PRIVATE KEY-----"
        ),
        "github_repository_allowlist": "acme/platform,hijaz7861/global-ai-platform",
        "ai_sandbox_attestation_hmac_key": "a" * 40,
        "ai_human_approval_hmac_key": "b" * 40,
    }
    values.update(overrides)
    return Settings(**values)


def test_github_repository_writes_are_disabled_by_default():
    config = Settings(environment="development", database_url="sqlite:///./test.db")
    assert config.ai_repository_writes_enabled is False
    validate_runtime_settings(config)


def test_github_repository_writes_reject_development_environment():
    config = _enabled_github_write_settings(environment="development")
    with pytest.raises(RuntimeError, match="staging or production"):
        validate_runtime_settings(config)


def test_github_repository_writes_require_postgresql():
    config = _enabled_github_write_settings(database_url="sqlite:///./test.db")
    with pytest.raises(RuntimeError, match="PostgreSQL nonce storage"):
        validate_runtime_settings(config)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("github_app_id", "not-numeric", "GITHUB_APP_ID"),
        ("github_app_installation_id", "", "GITHUB_APP_INSTALLATION_ID"),
        ("github_app_private_key_pem", "not-a-private-key", "GITHUB_APP_PRIVATE_KEY_PEM"),
        ("ai_sandbox_attestation_hmac_key", "short", "sandbox attestation key"),
        ("ai_human_approval_hmac_key", "short", "human approval key"),
        ("github_repository_allowlist", "", "owner/repository allowlist"),
        ("github_repository_allowlist", "acme/*", "owner/repository allowlist"),
    ],
)
def test_github_repository_writes_require_complete_secure_configuration(field, value, message):
    config = _enabled_github_write_settings(**{field: value})
    with pytest.raises(RuntimeError, match=message):
        validate_runtime_settings(config)


def test_github_repository_writes_accept_complete_staging_configuration():
    config = _enabled_github_write_settings(environment="staging", secret_key=None)
    validate_runtime_settings(config)
