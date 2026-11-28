"""Phase 8 cost and safety guards, checked statically (no AWS, no Terraform binary needed)."""

import re
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TF = ROOT / "deploy/terraform"
SCRIPTS = ["burst_up.sh", "burst_destroy.sh", "burst_preflight.sh", "check_manifests.sh"]


def _tf(mod: str) -> str:
    return "\n".join(p.read_text() for p in sorted((TF / mod).glob("*.tf")))


def test_no_nat_gateway_and_no_open_cidr() -> None:
    for mod in ("eks", "k3s"):
        src = _tf(mod)
        assert "aws_nat_gateway" not in src, f"{mod}: NAT gateway is ~$1/day idle"
        assert 'var.allowed_cidr != "0.0.0.0/0"' in src


def test_eks_cost_guards() -> None:
    src = _tf("eks")
    assert 'resource "aws_budgets_budget"' in src
    assert 'support_type = "STANDARD"' in src  # extended support is 6x the price
    assert "force_destroy = true" in src and "force_delete         = true" in src
    assert "recovery_window_in_days = 0" in src
    assert re.search(r"var\.node_count >= 1 && var\.node_count <= 4", src)
    assert "default_tags" in src


def test_k3s_dead_mans_switch() -> None:
    src = _tf("k3s")
    assert 'instance_initiated_shutdown_behavior = "terminate"' in src
    assert "shutdown -h +600" in src


def test_providers_pinned_exactly() -> None:
    for mod in ("eks", "k3s"):
        assert re.search(r'version\s*=\s*"6\.20\.0"', _tf(mod)), mod


def test_no_secret_values_in_terraform() -> None:
    for mod in ("eks", "k3s"):
        src = _tf(mod)
        assert "secret_string" not in src
        assert not re.search(r"@\w+\.\w+", src), "no email or other PII defaults"


def test_destroy_removes_controller_resources_before_terraform() -> None:
    src = (ROOT / "scripts/burst_destroy.sh").read_text()
    ingress = src.index("delete ingress")
    pg = src.index("delete postgrescluster")
    destroy = src.index("terraform -chdir=$TF destroy")
    assert ingress < destroy and pg < destroy
    assert "resourcegroupstaggingapi" in src and "TEARDOWN INCOMPLETE" in src


def test_up_requires_explicit_confirmation() -> None:
    src = (ROOT / "scripts/burst_up.sh").read_text()
    assert 'BURST_CONFIRM:-}" = "yes"' in src.split("terraform -chdir")[0]


def test_scripts_parse() -> None:
    bash = shutil.which("bash")
    assert bash
    for s in [*SCRIPTS, "../deploy/pgbackrest/restore_drill.sh"]:
        subprocess.run([bash, "-n", str(ROOT / "scripts" / s)], check=True)  # noqa: S603


def test_up_runs_preflight_before_terraform() -> None:
    src = (ROOT / "scripts/burst_up.sh").read_text()
    head = src.split("terraform -chdir")[0]
    assert "scripts/burst_preflight.sh" in head and 'AWS_PAGER=""' in head


def test_preflight_fails_without_env_file(tmp_path: Path) -> None:
    # A fresh terminal without `source ~/pap-burst.env`: preflight must refuse, not half-run.
    bash = shutil.which("bash")
    assert bash
    r = subprocess.run(  # noqa: S603
        [bash, "scripts/burst_preflight.sh"],
        cwd=ROOT,
        env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert r.returncode == 1
    assert "$LOAD_JWT_SECRET unset" in r.stdout and "PREFLIGHT FAILED" in r.stdout
