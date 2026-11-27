"""Postgres-checkpointed runs. thread_id is namespaced by tenant so tenants never share state."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Literal

import psycopg
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

# LangGraph's default durability is "async": the checkpoint for step N is written while step
# N+1 runs, so a worker killed in that window loses it. Measured: 8 of 30 kill/resume trials
# found nothing to resume with "async", 0 of 30 with "sync". Pass DURABILITY on every
# invoke/stream of a checkpointed graph.
DURABILITY: Literal["sync"] = "sync"


@asynccontextmanager
async def postgres_checkpointer(dsn: str) -> AsyncIterator[AsyncPostgresSaver]:
    async with AsyncPostgresSaver.from_conn_string(dsn) as saver:
        # setup() runs LangGraph's migrations (CREATE ... IF NOT EXISTS). Concurrent workers can
        # race on those (same failure as Phase 1B), so serialise with a session advisory lock.
        async with await psycopg.AsyncConnection.connect(dsn, autocommit=True) as lock:
            await lock.execute("SELECT pg_advisory_lock(hashtext('pap.migrate.checkpoints'))")
            try:
                await saver.setup()
            finally:
                await lock.execute("SELECT pg_advisory_unlock(hashtext('pap.migrate.checkpoints'))")
        yield saver


def run_config(tenant_id: str, run_id: str) -> dict[str, Any]:
    return {"configurable": {"thread_id": f"{tenant_id}:{run_id}"}}
