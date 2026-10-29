"""Regression guard: no manual/recovery REG.RU mutation before exact Home SHA."""
from pathlib import Path

WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/regru-deploy.yml"


def _deployment_job() -> str:
    text = WORKFLOW.read_text(encoding="utf-8")
    return text.split("  build-and-deploy:\n", 1)[1]


def test_sha_gate_precedes_any_mutating_ssh_step():
    job = _deployment_job()
    gate = job.index("name: Wait for exact home deployment before staging REG.RU")
    ssh = job.index("name: Prepare SSH identity")
    mutating = job.index("name: Prepare server and release HTTPS port")
    assert gate < ssh < mutating


def test_manual_and_recovery_runs_cannot_skip_live_sha_check():
    job = _deployment_job()
    gate = job.split("name: Wait for exact home deployment before staging REG.RU", 1)[1]
    gate = gate.split("\n      - name:", 1)[0]
    assert "if: github.event_name == 'push'" not in gate
    assert 'if [ "$GITHUB_EVENT_NAME" = "push" ]; then' in gate
    assert 'get("deployment_sha","")' in gate
    assert 'if [ "$live_sha" = "$GITHUB_SHA" ]; then' in gate
    assert 'if [ "$attempt" = 12 ]; then' in gate
    assert "exit 1" in gate


def test_push_requires_successful_exact_home_workflow():
    gate = _deployment_job().split(
        "name: Wait for exact home deployment before staging REG.RU", 1
    )[1].split("\n      - name:", 1)[0]
    assert 'head_sha=$GITHUB_SHA&event=push' in gate
    assert 'Deploy home secondary origin' in gate
    assert 'completed:success' in gate
    assert 'completed:*)' in gate


def test_diagnostics_only_does_not_run_mutating_sha_gate():
    diagnostics = WORKFLOW.read_text(encoding="utf-8").split(
        "  build-and-deploy:\n", 1
    )[0]
    assert "name: Wait for exact home deployment before staging REG.RU" not in diagnostics


def test_home_api_injects_image_sha_for_cross_host_preflight():
    root = WORKFLOW.parents[2]
    api = (root / "src/bankrotai/api.py").read_text(encoding="utf-8")
    home = (root / ".github/workflows/home-secondary-deploy.yml").read_text(encoding="utf-8")
    assert 'os.environ.get("BANKROTAI_DEPLOY_SHA", "")' in api
    assert '--env "BANKROTAI_DEPLOY_SHA=${env:GITHUB_SHA}"' in home
    assert 'deployment_sha' in api


def test_live_endpoint_returns_exact_runtime_commit(monkeypatch):
    import asyncio
    from bankrotai.api import liveness_check

    sha = "a" * 40
    monkeypatch.setenv("BANKROTAI_DEPLOY_SHA", sha)
    assert asyncio.run(liveness_check())["deployment_sha"] == sha
