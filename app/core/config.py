"""Environment-backed application settings."""
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Global AI Platform"
    app_version: str = "0.2.0"
    environment: str = "development"
    database_url: str = "sqlite:///./global_ai_platform.db"
    secret_key: str | None = None
    access_token_expire_minutes: int = Field(default=30, ge=5, le=1440)
    ai_live_calls_enabled: bool = False
    ai_api_key: str | None = None
    ai_base_url: str | None = None
    ai_model: str | None = None
    ai_timeout_seconds: int = Field(default=20, ge=1, le=120)
    ai_max_output_tokens: int = Field(default=512, ge=1, le=2048)
    ai_daily_request_limit: int = Field(default=0, ge=0, le=10000)

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
