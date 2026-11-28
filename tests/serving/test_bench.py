"""serving/bench.py against the OpenAI-compatible mock (stands in for vLLM), plus config checks.

The mock streams one chunk per word and sends no `usage`, so token counts come from chunks.
A real vLLM run (P7.3) is human-run on a rented GPU; nothing here measures GPU numbers.
"""

from __future__ import annotations

import json
import socket
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
import uvicorn
import yaml

from serving import bench

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def mock_base() -> Iterator[str]:
    from gateway.mock_llm import app

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = int(s.getsockname()[1])
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    while not server.started:
        time.sleep(0.02)
    yield f"http://127.0.0.1:{port}/v1"
    server.should_exit = True
    t.join(5)


def test_bench_reports_every_level(mock_base: str, tmp_path: Path) -> None:
    out = tmp_path / "r.json"
    argv = [
        "--base-url",
        mock_base,
        "--model",
        "local-llm",
        "--arm",
        "baseline",
        "--concurrency",
        "1",
        "4",
        "--requests",
        "8",
        "--out",
        str(out),
    ]
    result = bench.main(argv)
    assert json.loads(out.read_text()) == result
    assert [lv["concurrency"] for lv in result["levels"]] == [1, 4]
    for lv in result["levels"]:
        assert lv["requests"] == 8 and lv["errors"] == 0
        assert 0 < lv["ttft_p50_ms"] <= lv["ttft_p95_ms"]
        assert lv["tpot_p50_ms"] >= 0 and lv["output_tok_per_s"] > 0


def test_bench_counts_upstream_errors(mock_base: str) -> None:
    result = bench.main(
        ["--base-url", mock_base, "--model", "mock-flaky", "--concurrency", "2", "--requests", "4"]
    )
    assert result["levels"][0]["errors"] == 4 and result["levels"][0]["requests"] == 0


def test_summarise_tpot_excludes_first_token() -> None:
    lv = bench.summarise(1, [bench.Sample(ttft_s=0.5, total_s=1.5, output_tokens=11)], 0, 1.5)
    assert lv.ttft_p50_ms == pytest.approx(500)
    assert lv.tpot_p50_ms == pytest.approx(100)
    assert lv.output_tok_per_s == pytest.approx(11 / 1.5)


def test_litellm_routes_local_vllm_without_inline_secret() -> None:
    cfg = yaml.safe_load((ROOT / "gateway/litellm.yaml").read_text())
    entry = next(m for m in cfg["model_list"] if m["model_name"] == "local-vllm")
    p = entry["litellm_params"]
    assert p["model"] == "hosted_vllm/local-llm"
    assert p["api_base"] == "os.environ/VLLM_API_BASE"
    assert p["api_key"] == "os.environ/VLLM_API_KEY"
    fallbacks = {k: v for d in cfg["router_settings"]["fallbacks"] for k, v in d.items()}
    assert fallbacks["local-vllm"] == ["agent-fast"]


def test_serve_env_matches_spec_pin() -> None:
    env = dict(
        line.split("=", 1)
        for line in (ROOT / "serving/vllm.env").read_text().splitlines()
        if line and not line.startswith("#")
    )
    assert env["VLLM_IMAGE"] == "vllm/vllm-openai:v0.34.0"
    assert env["SERVED_NAME"] == "local-llm"
    assert "| vLLM | 0.34.0" in (ROOT / "docs/SPEC.md").read_text()


def _fake_vllm(tmp_path: Path, exit_code: int) -> dict[str, str]:
    import os

    fake = tmp_path / "vllm"
    fake.write_text(f'#!/bin/sh\necho "vllm $@"\nexit {exit_code}\n')
    fake.chmod(0o755)
    return {
        **os.environ,
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "HF_TOKEN": "x",
        "VLLM_API_KEY": "y",
        "VLLM_RUNNER": "native",
    }


def test_serve_sh_native_args_match_vllm_034(tmp_path: Path) -> None:
    import subprocess

    out = subprocess.run(  # noqa: S603 - fixed repo script, fake vllm
        [str(ROOT / "serving/serve.sh"), "prefix+eagle3"],
        env=_fake_vllm(tmp_path, 0),
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert out.startswith("vllm serve --model meta-llama/Llama-3.1-8B-Instruct")
    assert "--enable-prefix-caching" in out and '"method":"eagle3"' in out
    assert "--disable-log-requests" not in out  # removed in vLLM 0.34: server would not start


def test_run_all_marks_dead_arm_and_moves_on(tmp_path: Path) -> None:
    import shutil
    import subprocess

    repo = tmp_path / "repo"
    shutil.copytree(ROOT / "serving", repo / "serving", ignore=shutil.ignore_patterns("results"))
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    env = {**_fake_vllm(bin_dir, 1), "ARM_GAP_S": "0"}
    proc = subprocess.run(  # noqa: S603 - fixed repo script, fake vllm
        [str(repo / "serving/run_all.sh"), "baseline", "eagle3"],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.stdout.count("DID NOT RUN") == 2
    for arm in ("baseline", "eagle3"):
        data = json.loads((repo / f"serving/results/{arm}.json").read_text())
        assert data == {"arm": arm, "did_not_run": True}


def test_run_all_fails_fast_without_vllm(tmp_path: Path) -> None:
    import shutil
    import subprocess

    shutil.copytree(
        ROOT / "serving", tmp_path / "serving", ignore=shutil.ignore_patterns("results")
    )
    bin_dir = tmp_path / "bin"  # only what the preflight needs: no docker, no vllm
    bin_dir.mkdir()
    for tool in ("bash", "dirname"):
        (bin_dir / tool).symlink_to(shutil.which(tool) or f"/usr/bin/{tool}")
    env = {
        "PATH": str(bin_dir),
        "HF_TOKEN": "x",
        "VLLM_API_KEY": "y",
        "VLLM_BIN": str(tmp_path / "missing"),
    }
    proc = subprocess.run(  # noqa: S603 - fixed repo script
        [str(bin_dir / "bash"), str(tmp_path / "serving/run_all.sh")],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 127 and "runbook" in proc.stderr
    assert not (tmp_path / "serving/results").exists()
