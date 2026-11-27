"""Agent tools. Tenant id comes from graph state, never from model-supplied arguments."""

from __future__ import annotations

import ast
import operator
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from agent.model import ToolSpec
from retrieval.embedding import Embedder
from retrieval.pipeline import SearchConfig, search
from retrieval.store import PgStore


@dataclass(frozen=True)
class Tool:
    spec: ToolSpec
    fn: Callable[[str, dict[str, Any]], str]  # (tenant_id, args) -> observation
    requires_approval: bool = False


_OPS: dict[type, Callable[..., float]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.Mod: operator.mod,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}


def safe_eval(expr: str) -> float:
    """Arithmetic only: numbers and + - * / ** %. No names, calls or attributes."""
    if len(expr) > 200:
        raise ValueError("expression too long")

    def ev(n: ast.AST) -> float:
        if isinstance(n, ast.Expression):
            return ev(n.body)
        if isinstance(n, ast.Constant) and isinstance(n.value, int | float):
            return float(n.value)
        if isinstance(n, ast.BinOp) and type(n.op) in _OPS:
            if isinstance(n.op, ast.Pow) and abs(ev(n.right)) > 100:
                raise ValueError("exponent too large")
            return _OPS[type(n.op)](ev(n.left), ev(n.right))
        if isinstance(n, ast.UnaryOp) and type(n.op) in _OPS:
            return _OPS[type(n.op)](ev(n.operand))
        raise ValueError(f"unsupported expression: {type(n).__name__}")

    return ev(ast.parse(expr, mode="eval"))


def calculator() -> Tool:
    def fn(_tenant: str, args: dict[str, Any]) -> str:
        try:
            return repr(safe_eval(str(args["expression"])))
        except (ValueError, ZeroDivisionError, SyntaxError, KeyError) as e:
            return f"error: {e}"

    return Tool(
        ToolSpec(
            "calculator",
            "Evaluate an arithmetic expression (numbers, + - * / ** %).",
            {
                "type": "object",
                "properties": {"expression": {"type": "string"}},
                "required": ["expression"],
            },
        ),
        fn,
    )


def search_tool(store: PgStore, embedder: Embedder, cfg: SearchConfig | None = None) -> Tool:
    cfg = cfg or SearchConfig(hybrid=True, top_k=5)

    def fn(tenant: str, args: dict[str, Any]) -> str:
        hits = search(store, embedder, tenant, str(args["query"])[:500], cfg)
        if not hits:
            return "no results"
        return "\n".join(f"[{h.doc_id}] {h.content[:400]}" for h in hits)

    return Tool(
        ToolSpec(
            "search",
            "Hybrid search over this tenant's documents. Returns [doc_id] snippets.",
            {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
        ),
        fn,
    )


def fetch_doc_tool(store: PgStore, max_chars: int = 4000) -> Tool:
    def fn(tenant: str, args: dict[str, Any]) -> str:
        text = store.fetch_doc(tenant, str(args["doc_id"]))
        return text[:max_chars] if text else "not found"

    return Tool(
        ToolSpec(
            "fetch_doc",
            "Fetch the full text of one document by doc_id (from search results).",
            {
                "type": "object",
                "properties": {"doc_id": {"type": "string"}},
                "required": ["doc_id"],
            },
        ),
        fn,
    )


TICKETS = """
CREATE TABLE IF NOT EXISTS agent_tickets (
    tenant_id  text NOT NULL,
    id         text NOT NULL,
    title      text NOT NULL,
    body       text NOT NULL DEFAULT '',
    priority   text NOT NULL DEFAULT 'medium',
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, id)
);
"""


def migrate_tickets(dsn: str) -> None:
    import psycopg

    with psycopg.connect(dsn, prepare_threshold=None) as conn, conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(hashtext('pap.migrate.agent_tickets'))")
        conn.execute(TICKETS)


def create_ticket_tool(dsn: str) -> Tool:
    """A side-effecting action tool. Idempotent: the same ticket filed twice is one row,
    because a crashed `act` step re-runs its calls on resume (ADR 004)."""
    import hashlib

    import psycopg

    def fn(tenant: str, args: dict[str, Any]) -> str:
        title, body = str(args["title"]), str(args.get("body", ""))
        priority = str(args.get("priority", "medium"))
        tid = hashlib.sha256(f"{tenant}\x00{title}\x00{body}".encode()).hexdigest()[:12]
        with psycopg.connect(dsn, prepare_threshold=None) as conn:
            conn.execute(
                "INSERT INTO agent_tickets (tenant_id, id, title, body, priority) "
                "VALUES (%s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
                (tenant, tid, title, body, priority),
            )
        return f"ticket T-{tid} filed"

    return Tool(
        ToolSpec(
            "create_ticket",
            "File a support ticket for this tenant. Needs human approval.",
            {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "body": {"type": "string"},
                    "priority": {"type": "string", "enum": ["low", "medium", "high"]},
                },
                "required": ["title"],
            },
        ),
        fn,
        requires_approval=True,
    )
