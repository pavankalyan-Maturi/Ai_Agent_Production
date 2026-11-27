"""OTel GenAI spans for the agent loop. Semconv pinned: schema 1.44.0 via
opentelemetry-semantic-conventions 0.66b2 (GenAI attributes are "Development" status, so names
can still change; ADR 005). Content is never recorded: no prompts, tool args or outputs (PII off).
"""

from __future__ import annotations

from opentelemetry import trace
from opentelemetry.semconv._incubating.attributes import gen_ai_attributes as ga
from opentelemetry.semconv.schemas import Schemas
from opentelemetry.trace import Tracer

SCHEMA_URL = Schemas.V1_44_0.value
TENANT = "pap.tenant_id"

PROVIDERS = {"claude-": "anthropic", "scripted": "scripted"}


def tracer() -> Tracer:
    return trace.get_tracer("pap.agent", schema_url=SCHEMA_URL)


def provider_for(model: str) -> str:
    return next((p for k, p in PROVIDERS.items() if model.startswith(k)), "unknown")


__all__ = ["SCHEMA_URL", "TENANT", "ga", "provider_for", "tracer"]
