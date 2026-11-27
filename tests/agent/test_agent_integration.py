"""Needs Postgres. Set PAP_TEST_DATABASE_URL (CI job `integration` does)."""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import pytest
from langgraph.types import Command

from agent.graph import build_graph
from agent.model import ScriptedModel, ToolSpec
from agent.runner import DURABILITY, postgres_checkpointer, run_config
from agent.tools import Tool, fetch_doc_tool, search_tool
from retrieval.embedding import HashEmbedder
from retrieval.pipeline import index_documents
from retrieval.store import PgStore
from retrieval.types import Document
from tests.agent import crash_worker
from tests.agent.scripted import plan_policy

DSN = os.environ.get("PAP_TEST_DATABASE_URL")
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not DSN, reason="PAP_TEST_DATABASE_URL not set"),
]
ROOT = Path(__file__).resolve().parents[2]


def test_worker_kill_mid_run_resumes_from_checkpoint(tmp_path: Path) -> None:
    assert DSN
    log, flag = tmp_path / "log", tmp_path / "hang"
    flag.touch()
    thread = f"acme:{uuid.uuid4()}"
    proc = subprocess.Popen(  # noqa: S603 - fixed argv, our own module
        [sys.executable, "-m", "tests.agent.crash_worker", DSN, thread, str(log), str(flag)],
        cwd=ROOT,
    )
    try:
        deadline = time.monotonic() + 60
        while not (log.exists() and "hang" in log.read_text()):
            assert proc.poll() is None, "worker exited before reaching the hang point"
            assert time.monotonic() < deadline, "worker never reached the hang point"
            time.sleep(0.05)
        proc.send_signal(signal.SIGKILL)  # no cleanup, no final checkpoint write
        proc.wait(10)
    finally:
        if proc.poll() is None:
            proc.kill()
    flag.unlink()

    async def resume() -> tuple[Any, dict[str, Any], float]:
        async with postgres_checkpointer(DSN) as saver:
            g = crash_worker.graph(saver, str(log), str(flag))
            cfg = {"configurable": {"thread_id": thread}}
            before = await g.aget_state(cfg)
            t0 = time.perf_counter()
            out = await g.ainvoke(None, cfg, durability=DURABILITY)  # None = resume
            return before, out, time.perf_counter() - t0

    before, out, secs = asyncio.run(resume())
    assert before.next == ("act",)  # killed inside the third tool step
    assert out["status"] == "done" and out["answer"] == "done after work 3 ok"
    assert out["steps"] == 5
    lines = log.read_text().split()
    # Steps 0 and 1 ran once; step 2 is re-run (at-least-once); step 3 once.
    assert lines == ["acme", "0", "acme", "1", "acme", "2", "hang", "acme", "2", "acme", "3"]
    print(f"\nRESUME_SECONDS={secs:.3f}")


def test_hitl_survives_process_boundary() -> None:
    """Interrupt with one saver/connection, approve with a fresh one (a different worker)."""
    assert DSN
    seen: list[dict[str, Any]] = []

    def fn(_t: str, args: dict[str, Any]) -> str:
        seen.append(args)
        return "T-9"

    tool = Tool(ToolSpec("create_ticket", "", {}), fn, requires_approval=True)
    cfg = run_config("acme", str(uuid.uuid4()))

    def g(saver: Any) -> Any:
        model = ScriptedModel(plan_policy([("create_ticket", {"t": 1}), "filed {last}"]))
        return build_graph(model, [tool], checkpointer=saver)

    async def first() -> dict[str, Any]:
        async with postgres_checkpointer(DSN) as saver:
            return await g(saver).ainvoke(
                {"tenant_id": "acme", "question": "q"}, cfg, durability=DURABILITY
            )

    async def second() -> dict[str, Any]:
        async with postgres_checkpointer(DSN) as saver:
            return await g(saver).ainvoke(
                Command(resume={"approved": True}), cfg, durability=DURABILITY
            )

    assert "__interrupt__" in asyncio.run(first()) and seen == []
    out = asyncio.run(second())
    assert seen == [{"t": 1}] and out["answer"] == "filed T-9"


def test_search_and_fetch_tools_are_tenant_scoped() -> None:
    assert DSN
    emb = HashEmbedder(dim=384)
    store = PgStore(DSN, dim=384)
    store.migrate()
    a, b = f"ta-{uuid.uuid4().hex[:8]}", f"tb-{uuid.uuid4().hex[:8]}"
    try:
        index_documents(store, emb, [Document(a, "d1", "The reactor core runs at 300 C.")])
        index_documents(store, emb, [Document(b, "d9", "Secret reactor notes for B.")])
        s, f = search_tool(store, emb), fetch_doc_tool(store)
        assert "[d1]" in s.fn(a, {"query": "reactor core"})
        assert "d9" not in s.fn(a, {"query": "secret reactor notes"})
        assert "300 C" in (f.fn(a, {"doc_id": "d1"}) or "")
        assert f.fn(a, {"doc_id": "d9"}) == "not found"
    finally:
        store.delete_tenant(a)
        store.delete_tenant(b)


def test_pg_memory_bank_is_tenant_scoped() -> None:
    import numpy as np

    from agent.memory import MemoryItem, PgMemoryBank

    assert DSN
    emb = HashEmbedder(dim=384)
    bank = PgMemoryBank(DSN, dim=384)
    bank.migrate()
    a, b = f"ma-{uuid.uuid4().hex[:8]}", f"mb-{uuid.uuid4().hex[:8]}"
    it = MemoryItem("Bridge", "Search the bridge entity first, then its attribute.", "success")
    v = np.asarray(emb.embed_query(it.content))
    bank.add(a, it, v)
    bank.add(a, it, v)  # same id: no duplicate row
    assert bank.count(a) == 1 and bank.count(b) == 0
    hits = bank.search(a, v, 3)
    assert hits[0][0] == it and hits[0][1] > 0.99
    assert bank.search(b, v, 3) == []


def test_create_ticket_is_idempotent_and_tenant_scoped(tmp_path: Path) -> None:
    import psycopg

    from agent.governance import Governance
    from agent.tools import create_ticket_tool, migrate_tickets

    assert DSN
    migrate_tickets(DSN)
    gov = Governance.from_yaml(audit_path=str(tmp_path / "audit.db"))
    tool = create_ticket_tool(DSN)
    t = f"tk-{uuid.uuid4().hex[:8]}"
    args = {"title": "Disk full on db-1", "priority": "high"}
    a = gov.execute(tool, tenant_id=t, role="operator", args=args, call_id="c1", approver="alice")
    b = gov.execute(tool, tenant_id=t, role="operator", args=args, call_id="c1", approver="alice")
    assert a == b and a.startswith("ticket T-")  # a re-run after a crash files nothing new
    denied = gov.execute(tool, tenant_id=t, role="researcher", args=args, call_id="c2")
    assert '"decision": "blocked_policy"' in denied
    with psycopg.connect(DSN) as conn:
        n = conn.execute("SELECT count(*) FROM agent_tickets WHERE tenant_id = %s", (t,)).fetchone()
    assert n is not None and n[0] == 1
    gov.close()
