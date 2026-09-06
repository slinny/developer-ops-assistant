from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DOA_", env_file=".env", extra="ignore")
    webhook_secret: SecretStr
    api_token: SecretStr
    openai_api_key: SecretStr | None = None
    model: str = "gpt-4.1-mini"
    database_url: str = "sqlite+aiosqlite:///./developer_ops.db"
    attempt_timeout_seconds: float = Field(default=15, gt=0, le=300)
    processing_deadline_seconds: float = Field(default=45, gt=0, le=600)
    max_attempts: int = Field(default=3, ge=1, le=10)
    retry_base_seconds: float = Field(default=0.5, ge=0, le=30)
    retry_cap_seconds: float = Field(default=8, ge=0, le=60)
    retry_budget_seconds: float = Field(default=30, gt=0, le=600)
    max_concurrency: int = Field(default=4, ge=1, le=100)
    max_waiters: int = Field(default=16, ge=0, le=1000)
    capacity_wait_seconds: float = Field(default=2, gt=0, le=60)
    requests_per_window: int = Field(default=120, ge=1, le=100000)
    rate_window_seconds: float = Field(default=60, gt=0, le=3600)
    max_webhook_bytes: int = Field(default=262144, ge=1024, le=1048576)
    pricing_model: str | None = None
    pricing_version: str | None = None
    input_usd_per_million: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    cached_input_usd_per_million: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    output_usd_per_million: float | None = Field(default=None, ge=0, allow_inf_nan=False)
