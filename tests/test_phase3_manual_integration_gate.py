"""Guard against accidental production ingestion when merging an implementation PR.

Temporary safety assertion: preserve documented push paths and health
observability, but execute neither production job unless explicitly dispatched.
"""
from pathlib import Path


def test_phase3_ingestion_jobs_require_explicit_manual_dispatch() -> None:
    workflow = (
        Path(__file__).resolve().parents[1]
        / ".github"
        / "workflows"
        / "phase3-lite-full-reconcile.yml"
    ).read_text(encoding="utf-8")
    events = workflow.split("\non:\n", 1)[1].split("\npermissions:", 1)[0]
    assert "  workflow_dispatch:" in events
    assert "  push:" in events
    assert "    branches: [main]" in events

    job_section = workflow.split("\njobs:\n", 1)[1]
    deploy_job = job_section.split("\n  wait-home-deploy:\n", 1)[1].split(
        "\n  reconcile:\n", 1
    )[0]
    reconcile_job = job_section.split("\n  reconcile:\n", 1)[1]
    # Both gates are essential: if the first job changes, the second still
    # cannot mutate production from a push event.
    assert "    if: github.event_name == 'workflow_dispatch'" in deploy_job
    assert "    if: github.event_name == 'workflow_dispatch'" in reconcile_job
    assert "    if: github.event_name != 'push'" not in deploy_job
