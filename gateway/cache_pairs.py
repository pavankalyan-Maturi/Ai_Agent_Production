"""Labelled prompt pairs for the semantic-cache false-hit test (templated, deterministic).

`same=True`: a cached answer to `a` is correct for `b` (rewording). `same=False`: it is wrong
(different year, negation, entity or metric) but lexically close: these are the false hits.
"""

from __future__ import annotations

from typing import NamedTuple


class Pair(NamedTuple):
    a: str
    b: str
    same: bool
    kind: str


_ENT = ["ACME", "Globex", "Initech", "Umbrella", "Hooli"]
_MET = ["revenue", "headcount", "churn"]


def pairs() -> list[Pair]:
    out: list[Pair] = []
    for i, e in enumerate(_ENT):
        m, y = _MET[i % 3], 2020 + i
        base = f"What was {e} {m} in {y}?"
        out += [
            Pair(base, f"{e} {m} in {y}, what was it?", True, "reorder"),
            Pair(base, f"what was {e} {m} in {y}", True, "case-punct"),
            Pair(base, f"Please tell me: what was {e} {m} in {y}?", True, "filler"),
            Pair(base, f"What was {e} {m} in {y + 1}?", False, "number"),
            Pair(f"Is {e} profitable in {y}?", f"Is {e} not profitable in {y}?", False, "negation"),
            Pair(base, f"What was {_ENT[(i + 1) % 5]} {m} in {y}?", False, "entity"),
            Pair(base, f"What was {e} {_MET[(i + 1) % 3]} in {y}?", False, "metric"),
        ]
    return out
