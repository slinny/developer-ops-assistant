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
