from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="DOA_", env_file=".env", extra="ignore", allow_inf_nan=False
    )
    webhook_secret: SecretStr = Field(min_length=1)
    api_token: SecretStr = Field(min_length=1)
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
    worker_concurrency: int = Field(default=4, ge=1, le=32)
    worker_poll_seconds: float = Field(default=0.25, gt=0, le=30)
    job_lease_seconds: float = Field(default=30, ge=0.1, le=600)
    job_max_attempts: int = Field(default=5, ge=1, le=100)
    job_budget_seconds: float = Field(default=3600, gt=0)
    memory_path: str = "./event-memory-index"
    memory_enabled: bool = True
    queue_max_pending: int = Field(default=10000, ge=1)
    agent_repositories: list[str] = Field(default_factory=list)
    github_token: SecretStr | None = None
    agent_usd_per_million_upper_bound: float | None = Field(default=None, ge=0)
    agent_sync_concurrency: int = Field(default=2, ge=1, le=16)
