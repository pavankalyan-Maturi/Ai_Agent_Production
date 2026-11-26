# prod-agent-platform: rules for Claude threads

Read first, in order: `docs/handoff.md`, `docs/TASKS.md`, `docs/SPEC.md`, `docs/adr/`, `docs/RESULTS.md`.
If they disagree or are missing, stop and ask.

## Commands
- Install: `uv sync --frozen`
- Gate (must be green before push): `uv run ruff check . && uv run ruff format --check . && uv run mypy apps retrieval evals agent && uv run pytest -q`
- Stack: `docker build -t pap-api:dev . && scripts/compose_smoke.sh [--llmobs]`
- Probe latency: `uv run python scripts/bench_probes.py -n 1000`

## Rules
- One phase per branch and PR. Never merge, force-push or change scope without asking.
- Pins in `docs/SPEC.md` are binding; changing one needs an ADR.
- Never claim it works unless it ran. Write NOT MEASURED / UNVERIFIED otherwise.
- Never skip, weaken or xfail a test to get green without asking.
- No secrets in code. PII off in traces. Tenant id in every query and cache key (from Phase 1).
- No GPU and no cloud credentials here: vLLM is mocked behind the LiteLLM interface; AWS is runbooks only.
- End every thread with `docs/handoff.md` and a `docs/COSTS.md` row.
