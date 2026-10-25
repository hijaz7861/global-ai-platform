"""Environment-backed application settings."""
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


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

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
