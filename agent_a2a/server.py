"""A2A JSON-RPC binding over the LangGraph agent (P3.4, ADR 008).

- GET /.well-known/agent-card.json: the signed card (public, cacheable).
- GET /.well-known/jwks.json: the card-signing public key.
- POST /a2a/v1: JSON-RPC (SendMessage, GetTask, ...). Bearer JWT required. The tenant
  and owner come from the token; tasks are stored per owner, so one tenant can never
  read another tenant's task by id.

A2A callers run as the read-only `researcher` role. The run's tool calls still go through
mcp-rbac-gateway governance (ADR 006) when the graph is built with `governance=`.
"""

from __future__ import annotations

import uuid
from typing import Any

from a2a.helpers import new_task_from_user_message, new_text_part
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import (
    add_a2a_routes_to_fastapi,
    create_agent_card_routes,
    create_jsonrpc_routes,
)
from a2a.server.tasks import InMemoryTaskStore, TaskStore, TaskUpdater
from a2a.types import AgentCard
from fastapi import FastAPI
from starlette.authentication import (
    AuthCredentials,
    AuthenticationBackend,
    AuthenticationError,
    BaseUser,
)
from starlette.middleware.authentication import AuthenticationMiddleware
from starlette.requests import HTTPConnection
from starlette.responses import JSONResponse, Response

from agent.runner import DURABILITY, run_config
from agent_a2a.card import JWKS_PATH, RPC_PATH, SCOPE
from mcp_server.auth import JwtVerifier

ROLE = "researcher"


class TenantUser(BaseUser):
    def __init__(self, tenant: str, subject: str) -> None:
        self.tenant, self.subject = tenant, subject

    @property
    def is_authenticated(self) -> bool:
        return True

    @property
    def display_name(self) -> str:
        # The task store's owner key: per tenant and per user.
        return f"{self.tenant}:{self.subject}"


class BearerBackend(AuthenticationBackend):
    """Only the JSON-RPC path needs a token; the card and JWKS are public."""

    def __init__(self, verifier: JwtVerifier) -> None:
        self.verifier = verifier

    async def authenticate(self, conn: HTTPConnection) -> tuple[AuthCredentials, BaseUser] | None:
        if not conn.url.path.startswith(RPC_PATH):
            return None
        scheme, _, token = conn.headers.get("authorization", "").partition(" ")
        tok = await self.verifier.verify_token(token) if scheme.lower() == "bearer" else None
        if tok is None or not tok.claims:
            raise AuthenticationError("invalid_token")
        if SCOPE not in tok.scopes:
            raise AuthenticationError("insufficient_scope")
        return AuthCredentials(tok.scopes), TenantUser(tok.claims["tenant_id"], tok.subject or "")


def _auth_error(conn: HTTPConnection, exc: Exception) -> Response:
    code = str(exc)
    return JSONResponse(
        {"error": code},
        status_code=403 if code == "insufficient_scope" else 401,
        headers={"WWW-Authenticate": f'Bearer error="{code}"'},
    )


class GraphExecutor(AgentExecutor):
    """Runs one agent graph invocation per A2A task."""

    def __init__(self, graph: Any) -> None:
        self.graph = graph

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        user = context.call_context.user
        tenant = user.user_name.split(":", 1)[0]  # TenantUser.display_name, set from the token
        task = context.current_task
        if task is None:
            assert context.message is not None
            task = new_task_from_user_message(context.message)
            await event_queue.enqueue_event(task)
        updater = TaskUpdater(event_queue, task.id, task.context_id)
        await updater.start_work()
        question = context.get_user_input()
        cfg = run_config(tenant, f"a2a-{task.id}")
        out = await self.graph.ainvoke(
            {"tenant_id": tenant, "role": ROLE, "question": question},
            cfg,
            durability=DURABILITY,
        )
        if "__interrupt__" in out:  # cannot happen for the read-only role; fail closed
            await updater.failed(updater.new_agent_message([new_text_part("needs approval")]))
            return
        if out.get("status") != "done":
            reason = out.get("stop_reason") or out.get("failure") or "agent stopped"
            await updater.failed(updater.new_agent_message([new_text_part(f"failed: {reason}")]))
            return
        await updater.add_artifact([new_text_part(out.get("answer", ""))], name="answer")
        await updater.complete()

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        assert context.task_id and context.context_id
        await TaskUpdater(event_queue, context.task_id, context.context_id).cancel()


def build_a2a_app(
    graph: Any,
    card: AgentCard,
    jwks: dict[str, Any],
    verifier: JwtVerifier,
    *,
    task_store: TaskStore | None = None,
) -> FastAPI:
    app = FastAPI(title="pap A2A")
    handler = DefaultRequestHandler(
        agent_executor=GraphExecutor(graph),
        task_store=task_store or InMemoryTaskStore(),
        agent_card=card,
    )
    add_a2a_routes_to_fastapi(
        app,
        agent_card_routes=create_agent_card_routes(card, cache_control="public, max-age=300"),
        jsonrpc_routes=create_jsonrpc_routes(handler, rpc_url=RPC_PATH),
    )

    @app.get(JWKS_PATH, include_in_schema=False)
    async def keys() -> dict[str, Any]:
        return jwks

    app.add_middleware(
        AuthenticationMiddleware, backend=BearerBackend(verifier), on_error=_auth_error
    )
    return app


def new_kid() -> str:
    return uuid.uuid4().hex[:16]
