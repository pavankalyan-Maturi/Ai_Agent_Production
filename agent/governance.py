"""Zero-trust tool governance with mcp-rbac-gateway (imported as ``mcp_rbac_gateway``). ADR 006.

Every tool call the graph makes goes through the gateway's own pipeline, in its order:
policy (role allowlist + argument regex) -> input redaction + injection scan -> approval ->
run -> output redaction + injection scoring -> audit. These are the same ``check_call`` /
``check_result`` / ``record`` steps that ``@sentinel.guard`` runs. They are called directly
because ``guard`` cannot attach the tenant or the tool-call id to the audit row; this module
adds both (``session_id = "tenant:<id>"``, ``request_id = <tool_call_id>``) plus the W3C
``traceparent`` of the current span.

Approvals: the gateway's policy decides *whether* a call needs a human. The graph's
``interrupt()`` does the waiting, so a pending approval survives a restart (ADR 004). When the
graph resumes with an approval, ``execute`` hands the approver to the gateway, which then
records the call as ``approved`` with that identity. A call that needs approval but did not come
through the graph waits on the in-process ApprovalBroker, which the approvals API resolves.
"""

from __future__ import annotations

import asyncio
import contextvars
import json
import os
import time
from collections.abc import Coroutine, Mapping
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from mcp_rbac_gateway import (
    ApprovalBroker,
    ApprovalRequest,
    ApprovalResponse,
    AuditDecision,
    CallbackApprover,
    CallVerdict,
    RequestMeta,
    Sentinel,
    SentinelConfig,
    load_config,
)
from mcp_rbac_gateway.models import PolicyAction
from opentelemetry import propagate

from agent.tools import Tool

DENIAL_PREFIX = "error: tool_denied "
TRANSPORT = "langgraph"
DEFAULT_CONFIG = Path(__file__).resolve().parents[1] / "sentinel.yaml"

# (tool name, approver) for the one call the graph has just had approved by a human.
_PREAPPROVED: contextvars.ContextVar[tuple[str, str] | None] = contextvars.ContextVar(
    "pap_preapproved", default=None
)
_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="pap-governance")


def run_sync[T](coro: Coroutine[Any, Any, T]) -> T:
    """Run a coroutine from a sync graph node, with or without a loop in this thread."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    ctx = contextvars.copy_context()
    return _pool.submit(ctx.run, asyncio.run, coro).result()


def denial(tool: str, decision: str, reason: str) -> str:
    """The observation the model sees for a blocked call: structured, safe, no tool output."""
    body = {
        "tool": tool,
        "decision": decision,
        "reason": reason[:300],
        "retry": False,
        "hint": "This call is not permitted. Do not repeat it; continue without it or answer "
        "with what you have.",
    }
    return DENIAL_PREFIX + json.dumps(body)


def _traceparent() -> str | None:
    carrier: dict[str, str] = {}
    propagate.inject(carrier)
    return carrier.get("traceparent")


class Governance:
    def __init__(self, config: SentinelConfig) -> None:
        self.broker = ApprovalBroker()
        self.sentinel = Sentinel(
            config, approver=CallbackApprover(self._approve), broker=self.broker
        )

    @classmethod
    def from_yaml(
        cls, path: str | os.PathLike[str] | None = None, *, audit_path: str | None = None
    ) -> Governance:
        config = load_config(path or DEFAULT_CONFIG)
        if audit_path is not None:
            config.audit.path = audit_path
        return cls(config)

    async def _approve(self, request: ApprovalRequest) -> ApprovalResponse:
        pre = _PREAPPROVED.get()
        if pre is not None and pre[0] == request.tool:
            return ApprovalResponse(approved=True, approver=pre[1])
        future = self.broker.submit(request)  # resolved by POST /api/v1/security/approvals/...
        try:
            return await future
        finally:
            self.broker.discard(request.id)

    # ---------------------------------------------------------------- policy

    def needs_approval(self, role: str | None, tool: str, args: Mapping[str, Any]) -> str | None:
        """Why this call needs a human, or None. Denied calls need none: they never run."""
        decision = self.sentinel.policy.evaluate(
            self.sentinel.agent_or_default(role), tool, dict(args)
        )
        if decision.action is PolicyAction.DENY:
            return None
        return self.sentinel.gate.approval_reason(
            tool, dict(args), decision, self.sentinel.tool_annotations.get(tool)
        )

    def visible(self, role: str | None, tools: list[Tool]) -> list[Tool]:
        """Tools this role may see at all (allowlist), so the model is not offered the rest."""
        agent = self.sentinel.agent_or_default(role)
        return [t for t in tools if self.sentinel.policy.is_tool_visible(agent, t.spec.name)]

    # ------------------------------------------------------------- execution

    def execute(
        self,
        tool: Tool,
        *,
        tenant_id: str,
        role: str | None,
        args: Mapping[str, Any],
        call_id: str,
        approver: str | None = None,
    ) -> str:
        return run_sync(self._execute(tool, tenant_id, role, dict(args), call_id, approver))

    async def _record(self, verdict: CallVerdict, tenant_id: str, call_id: str, **kw: Any) -> None:
        await self.sentinel.record(
            verdict,
            transport=TRANSPORT,
            session_id=f"tenant:{tenant_id}",
            request_id=call_id,
            meta=RequestMeta(traceparent=_traceparent()),
            **kw,
        )

    async def _execute(
        self,
        tool: Tool,
        tenant_id: str,
        role: str | None,
        args: dict[str, Any],
        call_id: str,
        approver: str | None,
    ) -> str:
        name = tool.spec.name
        started = time.perf_counter()
        token = _PREAPPROVED.set((name, approver)) if approver else None
        try:
            verdict = await self.sentinel.check_call(role, name, args)
        finally:
            if token is not None:
                _PREAPPROVED.reset(token)

        def ms() -> float:
            return (time.perf_counter() - started) * 1000

        if not verdict.allowed:
            await self._record(verdict, tenant_id, call_id, latency_ms=ms())
            return denial(name, verdict.decision.value, verdict.reason)
        try:
            result = await asyncio.to_thread(tool.fn, tenant_id, verdict.arguments)
        except Exception as e:  # a tool failure is an observation, not a crash
            reason = f"{type(e).__name__}: {e}"[:500]
            await self._record(
                verdict,
                tenant_id,
                call_id,
                decision=AuditDecision.ERROR,
                reason=reason,
                latency_ms=ms(),
                is_error=True,
            )
            return f"error: {reason}"
        check = self.sentinel.check_result(result)
        if check.blocked:
            await self._record(
                verdict,
                tenant_id,
                call_id,
                decision=AuditDecision.BLOCKED_INJECTION,
                reason=check.reason,
                latency_ms=ms(),
                result_check=check,
            )
            return denial(name, AuditDecision.BLOCKED_INJECTION.value, check.reason or "blocked")
        await self._record(
            verdict, tenant_id, call_id, latency_ms=ms(), result=check.payload, result_check=check
        )
        return str(check.payload)

    def record_rejection(
        self,
        tool: str,
        *,
        tenant_id: str,
        role: str | None,
        args: Mapping[str, Any],
        call_id: str,
        approver: str | None,
    ) -> None:
        """A human rejected the call at the graph's approval pause; audit it like the gateway."""
        verdict = CallVerdict(
            agent_id=self.sentinel.agent_or_default(role),
            tool=tool,
            decision=AuditDecision.DENIED_BY_HUMAN,
            reason=f"denied by {approver or 'reviewer'} at the agent approval pause",
            arguments=self.sentinel.scanner.scan_input(dict(args)).payload,
            approver=approver,
        )
        run_sync(self._record(verdict, tenant_id, call_id))

    def close(self) -> None:
        self.sentinel.close()
