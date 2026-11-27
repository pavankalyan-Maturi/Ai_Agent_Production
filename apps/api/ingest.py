"""POST /v1/ingest: register an uploaded S3 object for ingestion (202, idempotent)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Header, Request
from pydantic import BaseModel, Field

from apps.api.auth import TENANT_RE, Ingester

router = APIRouter()

__all__ = ["TENANT_RE", "router"]


class IngestRequest(BaseModel):
    bucket: str = Field(min_length=3, max_length=63)
    key: str = Field(min_length=1, max_length=1024)
    etag: str = Field(default="", max_length=128)


class IngestResponse(BaseModel):
    idempotency_key: str
    published: bool


@router.post("/v1/ingest", status_code=202, response_model=IngestResponse)
def ingest(
    body: IngestRequest,
    request: Request,
    who: Ingester,
    idempotency_key: Annotated[str | None, Header()] = None,
) -> IngestResponse:
    # Tenant from the verified token (ADR 009), already checked against TENANT_RE.
    from apps.ingest.enqueue import enqueue_ingest

    celery_app, jobs = request.app.state.ingest_deps()
    r = enqueue_ingest(
        celery_app, jobs, who.tenant_id, body.bucket, body.key, body.etag, ikey=idempotency_key
    )
    return IngestResponse(idempotency_key=r.ikey, published=r.published)
