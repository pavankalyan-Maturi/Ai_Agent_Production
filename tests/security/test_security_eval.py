"""The P5.2 eval harness grades what it claims to (scripted model, no spend)."""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path

from agent.governance import Governance
from agent.model import Decision, Message, ScriptedModel, ToolCall
from evals.security.run import offline, run_case
from evals.security.suite import cases


def gullible(messages: Sequence[Message]) -> Decision:
    """Searches once, then obeys any canary it sees, via create_ticket for exfil docs."""
    tool = [m["content"] for m in messages if m["role"] == "tool"]
    if not tool:
        return Decision(tool_calls=(ToolCall("s", "search", {"query": "q"}),))
    hit = re.search(r"ZQX-\d+", tool[-1])
    if hit and "create_ticket" in tool[-1] and len(tool) == 1:
        return Decision(tool_calls=(ToolCall("t", "create_ticket", {"title": hit[0]}),))
    return Decision(text=f"Answer. {hit[0] if hit else ''}")


def test_suite_shape() -> None:
    cs = cases()
    assert len(cs) == 60 and sum(c.attack for c in cs) == 40
    assert len({c.canary for c in cs if c.attack}) == 40


def test_grading_and_scanner_arm(tmp_path: Path) -> None:
    by_id = {c.id: c for c in cases()}
    m = ScriptedModel(gullible)
    raw = run_case(m, None, by_id["direct_override-0"], spot=False)
    assert raw["hijacked"]
    exfil = run_case(m, None, by_id["exfil_tool-0"], spot=False)
    assert exfil["hijacked"] and exfil["ticket_attempted"]
    assert not run_case(m, None, by_id["benign-0-0"], spot=False)["hijacked"]
    gov = Governance.from_yaml(audit_path=str(tmp_path / "a.db"))
    try:  # the gateway blocks the doc, so the canary never reaches the model
        assert not run_case(m, gov, by_id["direct_override-0"], spot=True)["hijacked"]
        o = offline(gov, n_latency=5)
    finally:
        gov.close()
    assert o["false_block_rate"] == 0.0 and o["detection_by_family"]["direct_override"] == 1.0
