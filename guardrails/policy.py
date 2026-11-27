"""P5.4 step-level tool-call checker and trace-level policy. ADR 010.

mcp-rbac-gateway (ADR 006) decides whether a role may call a tool with these arguments. It
judges one call in isolation and cannot know where an argument came from. This checker runs
before the gateway and the approval pause, with the run's history:

step-level (one call, with taint):
  taint_to_sink         a control field of a sink (e.g. a ticket title) holds tokens that came
                        only from untrusted tool output
  tainted_var_to_sink   a control field references a tainted variable (plan/data mode)
trace-level (the whole run so far):
  sink_without_intent   a sink is called after untrusted data entered the run, and the user's
                        own request never asked for that kind of action
  sink_budget           more sink calls than the run allows
  denial_limit          the run is stopped after this many policy denials (a persistent
                        injection keeps retrying)

Reads (search, fetch_doc, calculator, extract) are never blocked here: a hijacked read leaks
nothing by itself. Data fields of a sink (e.g. a ticket body) may carry untrusted values, but
only when the user asked for that sink: that is the CaMeL-lite version of "the plan comes from
the trusted request".
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from guardrails.taint import Ledger, refs


@dataclass(frozen=True)
class Sink:
    intent: tuple[str, ...]  # words in the user's request that authorise this kind of action
    data_fields: frozenset[str] = frozenset()  # may carry untrusted values; the rest are control


@dataclass(frozen=True)
class Violation:
    rule: str
    reason: str  # tool and field names and counts only, never argument values


@dataclass(frozen=True)
class Policy:
    sinks: Mapping[str, Sink] = field(
        default_factory=lambda: {"create_ticket": Sink(("ticket",), frozenset({"body"}))}
    )
    max_sink_calls: int = 1
    max_denials: int = 2

    def check_step(
        self,
        name: str,
        args: Mapping[str, Any],
        ledger: Ledger,
        variables: Mapping[str, Mapping[str, Any]],
    ) -> Violation | None:
        sink = self.sinks.get(name)
        if sink is None:
            return None
        for k, v in args.items():
            if k in sink.data_fields:
                continue
            text = v if isinstance(v, str) else json.dumps(v)
            hits = ledger.tainted(text)
            if hits:
                return Violation(
                    "taint_to_sink", f"{name}.{k} carries {len(hits)} untrusted token(s)"
                )
            bad = [r for r in refs(v) if variables.get(r, {}).get("tainted")]
            if bad:
                return Violation("tainted_var_to_sink", f"{name}.{k} references a tainted variable")
        return None

    def check_trace(
        self, name: str, question: str, ledger: Ledger, prior_sink_calls: int
    ) -> Violation | None:
        sink = self.sinks.get(name)
        if sink is None:
            return None
        if prior_sink_calls >= self.max_sink_calls:
            return Violation("sink_budget", f"{name}: {prior_sink_calls} sink call(s) already")
        q = question.lower()
        if ledger.untrusted and not any(w in q for w in sink.intent):
            return Violation(
                "sink_without_intent", f"{name} after untrusted data; the request did not ask"
            )
        return None

    def check_calls(
        self,
        calls: Sequence[Mapping[str, Any]],
        *,
        question: str,
        ledger: Ledger,
        variables: Mapping[str, Mapping[str, Any]],
        prior_sink_calls: int,
    ) -> dict[str, Violation]:
        """Verdicts for one planner step: call_id -> violation for each denied call."""
        out: dict[str, Violation] = {}
        n = prior_sink_calls
        for c in calls:
            v = self.check_trace(c["name"], question, ledger, n) or self.check_step(
                c["name"], c["args"], ledger, variables
            )
            if v:
                out[c["id"]] = v
            elif c["name"] in self.sinks:
                n += 1
        return out
