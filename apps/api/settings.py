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

    # Ingestion (Phase 1): RabbitMQ broker for Celery, S3-compatible object store.
    broker_url: SecretStr = SecretStr("amqp://guest:guest@localhost:5672//")
    s3_endpoint_url: str | None = None  # None = AWS; SeaweedFS/MinIO locally
    s3_region: str = "us-east-1"
    s3_access_key_id: SecretStr | None = None
    s3_secret_access_key: SecretStr | None = None
    embedder: Literal["fastembed", "hash"] = "fastembed"  # hash = tests only
    embedding_dim: int = 384
    ingest_max_bytes: int = 5_000_000
    ingest_max_retries: int = 5

    # Tool governance (Phase 3, ADR 006): mcp-rbac-gateway policy and its SQLite audit trail.
    sentinel_config: str = "sentinel.yaml"
    sentinel_audit_db: str = ".sentinel/audit.db"
    # OIDC resource server (ADR 009): Keycloak issues, we verify. Neither key nor JWKS = 503.
    oidc_issuer: str = "http://localhost:8080/realms/pap"
    oidc_audience: str = "pap-api"
    oidc_jwks_url: str | None = None  # e.g. {issuer}/protocol/openid-connect/certs
    oidc_key: SecretStr | None = None  # tests/dev: PEM public key or HS256 secret
    oidc_algorithms: list[str] = ["RS256"]

    # P3.3 tool retrieval (RAG-MCP): offer the model only the top-k tools for the task.
    tool_retrieval_ragmcp: bool = False
    tool_retrieval_k: int = 5

    # A2A (Phase 3, ADR 008): card signing key (EC P-256 PEM) and public base URL.
    a2a_signing_key: SecretStr | None = None
    a2a_base_url: str = "http://localhost:8000"

    # MCP server (Phase 3, ADR 007). OAuth resource server: verifies JWTs it never issues.
    mcp_jwt_key: SecretStr | None = None  # HS256 secret or RS256/ES256 public key (PEM)
    mcp_jwt_algorithms: list[str] = ["HS256"]
    mcp_issuer: str = "http://localhost:9000"
    mcp_resource_url: str = "http://localhost:8001/mcp"  # the token `aud` (RFC 8707)
    mcp_allowed_hosts: list[str] = ["localhost:8001", "127.0.0.1:8001"]
    mcp_upstream_url: str = "http://localhost:8001/mcp"  # where the gateway forwards
    mcp_approval_token: SecretStr | None = None

    # Placeholder for the LiteLLM virtual key (Phase 4). Never logged.
    llm_api_key: SecretStr | None = None


@lru_cache
def get_settings() -> Settings:
    return Settings()
