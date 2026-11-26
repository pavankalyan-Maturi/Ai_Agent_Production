"""Typed runtime settings. Everything comes from env vars (prefix PAP_); no secrets in code."""

from functools import lru_cache
from typing import Literal

from pydantic import Field, PostgresDsn, RedisDsn, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PAP_", env_file=".env", extra="ignore")

    env: Literal["local", "ci", "staging", "prod"] = "local"
    service_name: str = "pap-api"
    log_level: str = "INFO"

    # Connects through PgBouncer (transaction pooling), so prepared statements stay off.
    database_url: PostgresDsn = Field(
        default=PostgresDsn("postgresql://pap:pap@localhost:6432/pap"),
    )
    redis_url: RedisDsn = Field(default=RedisDsn("redis://localhost:6379/0"))
    readiness_timeout_s: float = 2.0

    # OTel: endpoint unset means traces are not exported (no-op in unit tests).
    otel_exporter_otlp_endpoint: str | None = None
    # PII stays out of traces unless explicitly enabled (project rule 8).
    otel_capture_content: bool = False

    # Placeholder for the LiteLLM virtual key (Phase 4). Never logged.
    llm_api_key: SecretStr | None = None


@lru_cache
def get_settings() -> Settings:
    return Settings()
