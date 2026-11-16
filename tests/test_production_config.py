"""Production configuration validation tests."""
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from fastapi.testclient import TestClient

from app.core.config import Settings, settings, validate_runtime_settings
from app.main import app


def _private_pem(key) -> str:
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")


_TEST_GITHUB_APP_PRIVATE_KEY = _private_pem(
    rsa.generate_private_key(public_exponent=65537, key_size=2048)
)


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
        "github_app_private_key_pem": _TEST_GITHUB_APP_PRIVATE_KEY,
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


def test_github_repository_writes_reject_pem_markers_without_a_real_key():
    config = _enabled_github_write_settings(
        github_app_private_key_pem=(
            "-----BEGIN PRIVATE KEY-----\\nnot-a-key\\n-----END PRIVATE KEY-----"
        )
    )
    with pytest.raises(RuntimeError, match="valid unencrypted PEM RSA private key"):
        validate_runtime_settings(config)


def test_github_repository_writes_reject_non_rsa_private_keys():
    ec_key = ec.generate_private_key(ec.SECP256R1())
    config = _enabled_github_write_settings(github_app_private_key_pem=_private_pem(ec_key))
    with pytest.raises(RuntimeError, match="RSA private key"):
        validate_runtime_settings(config)


def test_github_repository_writes_reject_rsa_keys_below_2048_bits():
    weak_key = rsa.generate_private_key(public_exponent=65537, key_size=1024)
    config = _enabled_github_write_settings(github_app_private_key_pem=_private_pem(weak_key))
    with pytest.raises(RuntimeError, match="at least 2048 bits"):
        validate_runtime_settings(config)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("github_app_id", "not-numeric", "GITHUB_APP_ID"),
        ("github_app_id", "1" * 33, "GITHUB_APP_ID"),
        ("github_app_installation_id", "", "GITHUB_APP_INSTALLATION_ID"),
        ("github_app_installation_id", "1" * 33, "GITHUB_APP_INSTALLATION_ID"),
        ("ai_sandbox_attestation_hmac_key", "short", "sandbox attestation key"),
        ("ai_human_approval_hmac_key", "short", "human approval key"),
        ("github_repository_allowlist", "", "owner/repository allowlist"),
        ("github_repository_allowlist", "acme/*", "owner/repository allowlist"),
        ("github_repository_allowlist", "../repo", "owner/repository allowlist"),
        ("github_repository_allowlist", "owner/..", "owner/repository allowlist"),
        ("github_repository_allowlist", "owner/repo,", "empty or excessive"),
        ("github_repository_allowlist", "owner/repo,OWNER/REPO", "duplicates"),
        (
            "github_repository_allowlist",
            ",".join(f"owner/repo-{index}" for index in range(51)),
            "empty or excessive",
        ),
    ],
)
def test_github_repository_writes_reject_unsafe_configuration(field, value, message):
    config = _enabled_github_write_settings(**{field: value})
    with pytest.raises(RuntimeError, match=message):
        validate_runtime_settings(config)


def test_github_repository_write_secrets_are_redacted_from_settings_repr():
    config = _enabled_github_write_settings()
    rendered = repr(config)
    assert "BEGIN PRIVATE KEY" not in rendered
    assert "a" * 40 not in rendered
    assert "b" * 40 not in rendered


def test_github_repository_writes_accept_complete_staging_configuration():
    config = _enabled_github_write_settings(environment="staging", secret_key=None)
    validate_runtime_settings(config)
