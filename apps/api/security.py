"""/api/v1/security: pending tool approvals and the governance audit trail (ADR 006).

Protected by OIDC bearer tokens with the ``pap:admin`` scope (ADR 009). The audit trail is the
caller's own tenant only: the tenant comes from the token. With auth unconfigured the routes
answer 503, so they are never open by accident.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from mcp_rbac_gateway import AuditDecision, create_approval_router
from pydantic import BaseModel

from apps.api.auth import ADMIN, Admin, require

PREFIX = "/api/v1/security"
BLOCKED = [d.value for d in AuditDecision if d.is_block]


class AuditRow(BaseModel):
    id: int
    ts: float
    agent_id: str
    tool: str
    decision: str
    reason: str | None
    approver: str | None
    latency_ms: float | None
    injection_score: float | None
    redactions: int | None
    request_id: str | None
    traceparent: str | None
    arguments: str | None  # redacted by the gateway before it was written


# Columns of mcp-rbac-gateway 0.2.x's `events` table. Read directly because AuditLog.query()
# cannot filter by session_id, which is where the tenant is recorded (pinned <0.3).
_COLS = (
    "id, ts, agent_id, tool, decision, reason, approver, latency_ms, injection_score, "
    "redactions, request_id, traceparent, arguments"
)


def read_audit(
    db: Path, tenant_id: str, *, limit: int, blocked_only: bool, tool: str | None
) -> list[AuditRow]:
    if not db.exists():
        return []
    sql = f"SELECT {_COLS} FROM events WHERE session_id = ?"  # noqa: S608 - fixed column list
    params: list[Any] = [f"tenant:{tenant_id}"]
    if blocked_only:
        sql += f" AND decision IN ({','.join('?' * len(BLOCKED))})"
        params += BLOCKED
    if tool:
        sql += " AND tool = ?"
        params.append(tool)
    sql += " ORDER BY ts DESC, id DESC LIMIT ?"
    params.append(limit)
    with sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(sql, params).fetchall()
    return [AuditRow(**dict(r)) for r in rows]


def build_router(get_governance: Any) -> APIRouter:
    """get_governance() -> agent.governance.Governance, built once per process."""
    router = APIRouter(prefix=PREFIX, tags=["security"], dependencies=[Depends(require(ADMIN))])

    # The gateway's own approvals API: GET /approvals, GET/POST /approvals/{id}/approve|deny.
    # token=None because require(ADMIN) already guards the whole prefix.
    class _LazyBroker:
        def __getattr__(self, name: str) -> Any:
            return getattr(get_governance().broker, name)

    router.include_router(create_approval_router(_LazyBroker()))  # type: ignore[arg-type]

    @router.get("/audit", response_model=list[AuditRow])
    def audit(
        request: Request,
        who: Admin,
        limit: Annotated[int, Query(ge=1, le=500)] = 50,
        blocked_only: bool = False,
        tool: str | None = None,
    ) -> list[AuditRow]:
        db = Path(request.app.state.settings.sentinel_audit_db)
        return read_audit(db, who.tenant_id, limit=limit, blocked_only=blocked_only, tool=tool)

    return router
