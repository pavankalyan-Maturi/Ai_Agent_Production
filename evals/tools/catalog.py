"""Synthetic tool catalog for the tool-count scaling eval (P3.3).

Each tool is "<verb>_<object>" with a one-line description, across distinct domains, so
the catalog can grow to hundreds of plausible, non-duplicate tools. Each query names the
need in different words than the tool's own name where possible, and has exactly one
gold tool. Deterministic: the same n gives the same catalog.
"""

from __future__ import annotations

import itertools
import random

from agent.model import ToolSpec

DOMAINS: dict[str, list[tuple[str, str]]] = {
    "invoice": [("billing", "customer invoices and amounts due")],
    "ticket": [("support", "customer support tickets and their status")],
    "shipment": [("logistics", "parcel shipments, carriers and delivery dates")],
    "employee": [("hr", "employee records, managers and departments")],
    "calendar_event": [("calendar", "meetings and calendar events")],
    "repository": [("code", "source code repositories and commits")],
    "deployment": [("ops", "service deployments and rollbacks")],
    "metric": [("monitoring", "service metrics, latency and error rates")],
    "contract": [("legal", "signed contracts, parties and renewal dates")],
    "inventory_item": [("warehouse", "stock levels of warehouse items")],
    "lead": [("sales", "sales leads and pipeline stages")],
    "expense": [("finance", "employee expense reports and receipts")],
    "flight": [("travel", "flight bookings and itineraries")],
    "patient_record": [("clinic", "patient visit notes and prescriptions")],
    "vulnerability": [("security", "CVE findings on hosts and images")],
    "dataset": [("data", "warehouse tables, schemas and row counts")],
    "invoice_payment": [("payments", "card and bank payments against invoices")],
    "survey_response": [("research", "customer survey answers and NPS")],
    "vehicle": [("fleet", "company vehicles, mileage and service history")],
    "course": [("learning", "training courses and enrolments")],
}
VERBS: dict[str, tuple[str, list[str]]] = {
    "list": ("List", ["show me all", "which", "give me every"]),
    "get": ("Fetch one", ["look up the", "details of the", "open the"]),
    "create": ("Create a new", ["open a new", "add a", "file a"]),
    "update": ("Update fields of an existing", ["change the", "edit the", "modify the"]),
    "delete": ("Delete an existing", ["remove the", "get rid of the", "cancel the"]),
    "search": ("Full-text search over", ["find any", "search for", "hunt down"]),
    "export": ("Export as CSV the", ["download a spreadsheet of", "dump", "export"]),
    "summarize": ("Summarize trends in", ["give an overview of", "summarise", "recap"]),
}
# Our real read-only tools are part of every catalog.
REAL = [
    ToolSpec(
        "search", "Hybrid search over this tenant's documents. Returns [doc_id] snippets.", {}
    ),
    ToolSpec(
        "fetch_doc", "Fetch the full text of one document by doc_id (from search results).", {}
    ),
    ToolSpec("calculator", "Evaluate an arithmetic expression.", {}),
]


def all_tools() -> list[tuple[ToolSpec, str, str, list[str]]]:
    out = []
    for (obj, [(_, what)]), (verb, (vdesc, phrasings)) in itertools.product(
        DOMAINS.items(), VERBS.items()
    ):
        name = f"{verb}_{obj}"
        out.append((ToolSpec(name, f"{vdesc} {what}.", {}), obj, what, phrasings))
    return out


def catalog(n: int, seed: int = 0) -> tuple[list[ToolSpec], list[tuple[str, str]]]:
    """n tools (3 real + n-3 synthetic) and one query per synthetic tool: (query, gold)."""
    rng = random.Random(seed)
    pool = all_tools()
    rng.shuffle(pool)
    picked = pool[: max(0, n - len(REAL))]
    queries = []
    for spec, obj, what, phrasings in picked:
        lead = rng.choice(phrasings)
        noun = obj.replace("_", " ")
        queries.append((f"{lead} {noun} ({what.split(',')[0]}) for account 4411", spec.name))
    queries += [
        ("what does our documentation say about the reactor cooling loop", "search"),
        ("compute 17.5% of 2,340", "calculator"),
    ]
    return REAL + [p[0] for p in picked], queries
