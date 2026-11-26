"""POST /v1/ingest: register an uploaded S3 object for ingestion (202, idempotent)."""

from __future__ import annotations

import re
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, Field

router = APIRouter()

TENANT_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")


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
    x_tenant_id: Annotated[str, Header()],
    idempotency_key: Annotated[str | None, Header()] = None,
) -> IngestResponse:
    # Tenant comes from a header until auth lands (Phase 5); validated, never free text.
    if not TENANT_RE.match(x_tenant_id):
        raise HTTPException(422, "invalid X-Tenant-Id")
    from apps.ingest.enqueue import enqueue_ingest

    celery_app, jobs = request.app.state.ingest_deps()
    r = enqueue_ingest(
        celery_app, jobs, x_tenant_id, body.bucket, body.key, body.etag, ikey=idempotency_key
    )
    return IngestResponse(idempotency_key=r.ikey, published=r.published)
