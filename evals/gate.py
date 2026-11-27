"""P6.4 eval-gate: trajectory metrics, MAST histogram, judge + kappa, paired-bootstrap gate.

usage: uv run python -m evals.gate --traj results/agent/trajectories.jsonl [--judge]
Reads `evals/gate.toml`, `evals/baselines/agent_off.json` (per-question baseline) and
`evals/labels/hotpot_correctness.csv` (human labels). Writes results/gate/{report.md,
gate.json, label_pool.csv, baseline_candidate.json}. Exits 1 only on an enforced failure.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import tomllib
from pathlib import Path
from typing import Any

from evals import judge as J
from evals import trajectory as T
from evals.agent.run import paired_ci

LABELS = Path("evals/labels/hotpot_correctness.csv")
BASELINE = Path("evals/baselines/agent_off.json")


def per_question(
    recs: list[dict[str, Any]], tm: list[dict[str, Any]], verdicts: dict[str, bool | None]
) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {}
    for r, m in zip(recs, tm, strict=True):
        row = {
            "f1": float(r["outcome"]["f1"]),
            "usd": float(r["outcome"]["usd"]),
            "evidence_recall": float(m["evidence_recall"]),
            "redundant_calls": float(m["redundant_calls"]),
        }
        v = verdicts.get(r["qid"])
        if v is not None:
            row["judge_correct"] = float(v)
        out[r["qid"]] = row
    return out


def compare(
    name: str,
    spec: dict[str, Any],
    cur: dict[str, dict[str, float]],
    base: dict[str, dict[str, float]],
) -> dict[str, Any]:
    ids = sorted(q for q in cur if q in base and name in cur[q] and name in base[q])
    if len(ids) < 2:
        return {"metric": name, "status": "NOT MEASURED", "why": "no paired baseline"}
    b = [base[q][name] for q in ids]
    c = [cur[q][name] for q in ids]
    mean, lo, hi = paired_ci(b, c)
    scale = abs(statistics.fmean(b)) if spec.get("relative") else 1.0
    effect = mean / scale if scale else 0.0
    if spec["direction"] == "higher":
        regressed = hi < 0 and -effect >= spec["min_effect"]
    else:
        regressed = lo > 0 and effect >= spec["min_effect"]
    return {
        "metric": name,
        "n": len(ids),
        "base": round(statistics.fmean(b), 4),
        "pr": round(statistics.fmean(c), 4),
        "delta_ci": [round(mean, 4), round(lo, 4), round(hi, 4)],
        "status": "REGRESSED" if regressed else "ok",
    }


def run_gate(
    cfg: dict[str, Any],
    cur: dict[str, dict[str, float]],
    base: dict[str, dict[str, float]],
    calib: dict[str, Any],
    spend_usd: float,
) -> tuple[list[dict[str, Any]], list[str]]:
    g = cfg["gate"]
    rows, failures = [], []
    kappa_ok = calib.get("n", 0) >= g["min_labels"] and calib["judge_kappa"][0] >= g["min_kappa"]
    for name, spec in cfg["metric"].items():
        if base:
            r = compare(name, spec, cur, base)
        elif "scalar_baseline" in spec:
            vals = [v[name] for v in cur.values() if name in v]
            pr = statistics.fmean(vals) if vals else 0.0
            bad = pr < spec["scalar_baseline"] - spec["scalar_tolerance"]
            r = {
                "metric": name,
                "n": len(vals),
                "base": spec["scalar_baseline"],
                "pr": round(pr, 4),
                "status": "REGRESSED" if bad else "ok",
                "why": "scalar baseline (no per-question baseline yet)",
            }
        else:
            r = {"metric": name, "status": "NOT MEASURED", "why": "no per-question baseline"}
        enforced = bool(spec.get("enforce")) and (kappa_ok or not spec.get("requires_kappa"))
        r["enforced"] = enforced
        if spec.get("requires_kappa") and not kappa_ok:
            r["why"] = f"report-only: judge not calibrated (labels {calib.get('n', 0)})"
        if r["status"] == "REGRESSED" and enforced:
            failures.append(f"{name}: {r}")
        rows.append(r)
    if spend_usd > g["cost_cap_usd"] and g["enforce_cost_cap"]:
        failures.append(f"spend ${spend_usd:.2f} > cost cap ${g['cost_cap_usd']:.2f}")
    return rows, failures


def to_markdown(
    rows: list[dict[str, Any]], calib: dict[str, Any], traj_md: str, spend: float
) -> str:
    lines = [
        f"eval-gate: new model spend ${spend:.3f}.",
        "",
        "| Metric | Base | PR | Δ [95% CI] | Status | Enforced |",
        "|---|---|---|---|---|---|",
    ]
    for r in rows:
        d = r.get("delta_ci")
        ds = f"{d[0]:+.4f} [{d[1]:+.4f}, {d[2]:+.4f}]" if d else r.get("why", "-")
        lines.append(
            f"| {r['metric']} | {r.get('base', '-')} | {r.get('pr', '-')} | {ds} | "
            f"{r['status']} | {'yes' if r['enforced'] else 'no'} |"
        )
    lines += ["", "Judge calibration vs human labels: "]
    if calib.get("n"):
        lines[-1] += (
            f"n={calib['n']}, judge kappa {calib['judge_kappa']}, F1>=0.5 rule kappa "
            f"{calib['f1_rule_kappa']} (point [95% CI])."
        )
    else:
        lines[-1] += "NOT MEASURED (no human labels in evals/labels/ yet)."
    return "\n".join([*lines, "", traj_md])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--traj", type=Path, default=Path("results/agent/trajectories.jsonl"))
    ap.add_argument("--ablation", type=Path, default=Path("results/agent/ablation.json"))
    ap.add_argument("--config", type=Path, default=Path("evals/gate.toml"))
    ap.add_argument("--out", type=Path, default=Path("results/gate"))
    ap.add_argument("--cache", type=Path, default=Path(".cache"))
    ap.add_argument("--judge", action="store_true", help="run the Haiku judge (cached)")
    ap.add_argument("--max-usd", type=float, default=0.25, help="judge spend cap")
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    cfg = tomllib.loads(a.config.read_text())
    if not a.traj.exists():
        msg = "eval-gate: NOT MEASURED (no trajectories: no ANTHROPIC_API_KEY or agent run failed)"
        (a.out / "report.md").write_text(msg + "\n")
        print(msg)
        return

    recs = T.load(a.traj)
    verdicts: dict[str, bool | None] = {}
    judge_usd = 0.0
    if a.judge and os.environ.get("ANTHROPIC_API_KEY"):
        from agent.model import AnthropicModel
        from evals.agent.run import HAIKU_EXTRA, MODEL, CachedModel, Spend

        jspend = Spend(a.max_usd)
        jm = CachedModel(
            AnthropicModel(MODEL, max_tokens=8, extra=HAIKU_EXTRA),
            a.cache / "agent_llm" / f"judge-{MODEL}.jsonl",
            jspend,
        )
        verdicts = J.judge(jm, recs)
        judge_usd = jspend.usd
    agent_usd = 0.0
    if a.ablation.exists():
        agent_usd = float(json.loads(a.ablation.read_text())["meta"]["spend_usd"])

    judged = {q: v for q, v in verdicts.items() if v is not None}
    ts = T.summarize(recs, judged or None)
    cur = per_question(recs, ts["per_question"], verdicts)
    base = json.loads(BASELINE.read_text())["per_question"] if BASELINE.exists() else {}
    f1 = {r["qid"]: float(r["outcome"]["f1"]) for r in recs}
    calib = J.calibrate(J.load_labels(LABELS), verdicts, f1)
    spend = agent_usd + judge_usd
    rows, failures = run_gate(cfg, cur, base, calib, spend)

    md = to_markdown(rows, calib, T.to_markdown(ts), spend)
    (a.out / "report.md").write_text(md + "\n")
    J.write_label_pool(a.out / "label_pool.csv", recs)
    (a.out / "baseline_candidate.json").write_text(json.dumps({"per_question": cur}))
    ts.pop("per_question")
    (a.out / "gate.json").write_text(
        json.dumps({"rows": rows, "calibration": calib, "trajectory": ts}, indent=1)
    )
    print(md)
    for f in failures:
        print(f"EVAL GATE FAILED: {f}", file=sys.stderr)
    if failures:
        sys.exit(1)


if __name__ == "__main__":
    main()
