"""Environment-backed application settings."""
import re

from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


_REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$")
_MAX_GITHUB_REPOSITORIES = 50


class Settings(BaseSettings):
    app_name: str = "Global AI Platform"
    app_version: str = "0.2.0"
    environment: str = "development"
    database_url: str = "sqlite:///./global_ai_platform.db"
    secret_key: str | None = None
    previous_secret_keys: str = ""
    access_token_expire_minutes: int = Field(default=30, ge=5, le=1440)
    refresh_token_expire_days: int = Field(default=30, ge=1, le=90)
    mfa_enrollment_expire_minutes: int = Field(default=10, ge=5, le=30)
    order_payment_expire_minutes: int = Field(default=30, ge=5, le=10080)
    password_reset_token_expire_minutes: int = Field(default=30, ge=5, le=120)
    auth_login_attempt_limit: int = Field(default=10, ge=3, le=100)
    auth_login_window_minutes: int = Field(default=15, ge=1, le=1440)
    auth_account_lockout_threshold: int = Field(default=10, ge=3, le=100)
    auth_account_lockout_minutes: int = Field(default=15, ge=1, le=1440)
    password_reset_request_limit: int = Field(default=5, ge=1, le=100)
    password_reset_window_minutes: int = Field(default=15, ge=1, le=1440)
    email_verification_token_expire_minutes: int = Field(default=30, ge=5, le=120)
    require_email_verification: bool = True
    auth_register_attempt_limit: int = Field(default=5, ge=1, le=100)
    auth_register_window_minutes: int = Field(default=60, ge=1, le=1440)
    smtp_host: str | None = None
    smtp_port: int = Field(default=587, ge=1, le=65535)
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_from_email: str | None = None
    smtp_starttls: bool = True
    platform_admin_emails: str = ""
    trusted_proxy_ips: str = ""
    ai_live_calls_enabled: bool = False
    ai_auto_failover_enabled: bool = False
    ai_primary_provider: str = "openai-compatible"
    ai_fallback_provider: str | None = None
    ai_allow_local_endpoints: bool = False
    ai_api_key: str | None = None
    ai_base_url: str | None = None
    ai_model: str | None = None
    ai_fallback_api_key: str | None = None
    ai_fallback_base_url: str | None = None
    ai_fallback_model: str | None = None
    ai_timeout_seconds: int = Field(default=20, ge=1, le=120)
    ai_max_output_tokens: int = Field(default=512, ge=1, le=2048)
    ai_max_response_bytes: int = Field(default=1048576, ge=1024, le=10485760)
    ai_daily_request_limit: int = Field(default=0, ge=0, le=10000)
    ai_daily_token_limit: int = Field(default=0, ge=0, le=10000000)
    ai_repository_writes_enabled: bool = False
    github_app_id: str | None = None
    github_app_installation_id: str | None = None
    github_app_private_key_pem: SecretStr | None = None
    github_repository_allowlist: str = ""
    ai_sandbox_attestation_hmac_key: SecretStr | None = None
    ai_human_approval_hmac_key: SecretStr | None = None

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


def _validate_github_app_private_key(value: str) -> None:
    """Require a parseable, unencrypted RSA key suitable for GitHub App RS256."""
    try:
        private_key = serialization.load_pem_private_key(
            value.encode("utf-8"),
            password=None,
        )
    except (TypeError, ValueError, UnsupportedAlgorithm):
        raise RuntimeError(
            "GITHUB_APP_PRIVATE_KEY_PEM must be a valid unencrypted PEM RSA private key"
        ) from None
    if not isinstance(private_key, rsa.RSAPrivateKey) or private_key.key_size < 2048:
        raise RuntimeError(
            "GITHUB_APP_PRIVATE_KEY_PEM must contain an RSA private key of at least 2048 bits"
        )


def _parse_github_repository_allowlist(value: str) -> tuple[str, ...]:
    """Parse a bounded, unambiguous owner/repository allowlist."""
    if not isinstance(value, str) or not value:
        raise RuntimeError("GitHub repository writes require an explicit owner/repository allowlist")

    raw_entries = value.split(",")
    if (
        len(raw_entries) > _MAX_GITHUB_REPOSITORIES
        or any(not item.strip() for item in raw_entries)
    ):
        raise RuntimeError("GitHub repository allowlist contains empty or excessive entries")

    repositories = tuple(item.strip() for item in raw_entries)
    if len({item.casefold() for item in repositories}) != len(repositories):
        raise RuntimeError("GitHub repository allowlist must not contain duplicates")

    for item in repositories:
        if not _REPOSITORY.fullmatch(item):
            raise RuntimeError("GitHub repository allowlist must use exact owner/repository names")
        owner, repository = item.split("/", 1)
        if owner in {".", ".."} or repository in {".", ".."}:
            raise RuntimeError("GitHub repository allowlist contains an unsafe path segment")
        if owner.startswith(".") or owner.endswith(".") or repository.startswith(".") or repository.endswith("."):
            raise RuntimeError("GitHub repository allowlist contains an unsafe path segment")

    return repositories


def validate_runtime_settings(config: "Settings") -> None:
    """Fail closed on unsafe production and repository-write configuration."""
    environment = config.environment.strip().lower()
    is_postgres = config.database_url.lower().startswith(("postgresql://", "postgresql+"))

    if environment == "production":
        if not config.secret_key or len(config.secret_key) < 32:
            raise RuntimeError("Production requires a SECRET_KEY of at least 32 characters")
        if not is_postgres:
            raise RuntimeError("Production requires PostgreSQL; SQLite is for local development only")

    if not config.ai_repository_writes_enabled:
        return
    if environment not in {"staging", "production"}:
        raise RuntimeError("GitHub repository writes require staging or production environment")
    if not is_postgres:
        raise RuntimeError("GitHub repository writes require PostgreSQL nonce storage")

    if (
        not config.github_app_id
        or not config.github_app_id.isdigit()
        or len(config.github_app_id) > 32
    ):
        raise RuntimeError("GitHub repository writes require a numeric GITHUB_APP_ID")
    if (
        not config.github_app_installation_id
        or not config.github_app_installation_id.isdigit()
        or len(config.github_app_installation_id) > 32
    ):
        raise RuntimeError("GitHub repository writes require a numeric GITHUB_APP_INSTALLATION_ID")

    private_key = (
        config.github_app_private_key_pem.get_secret_value()
        if config.github_app_private_key_pem else ""
    )
    attestation_key = (
        config.ai_sandbox_attestation_hmac_key.get_secret_value()
        if config.ai_sandbox_attestation_hmac_key else ""
    )
    approval_key = (
        config.ai_human_approval_hmac_key.get_secret_value()
        if config.ai_human_approval_hmac_key else ""
    )
    _validate_github_app_private_key(private_key)

    if len(attestation_key.encode("utf-8")) < 32:
        raise RuntimeError("GitHub repository writes require a 32-byte sandbox attestation key")
    if len(approval_key.encode("utf-8")) < 32:
        raise RuntimeError("GitHub repository writes require a 32-byte human approval key")

    _parse_github_repository_allowlist(config.github_repository_allowlist)


settings = Settings()
