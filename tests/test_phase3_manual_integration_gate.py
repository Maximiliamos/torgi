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
    deploy_job = job_section.split("  wait-home-deploy:\n", 1)[1].split(
        "\n  reconcile:\n", 1
    )[0]
    reconcile_job = job_section.split("\n  reconcile:\n", 1)[1]
    # Both gates are essential: if the first job changes, the second still
    # cannot mutate production from a push event.
    assert "    if: github.event_name == 'workflow_dispatch'" in deploy_job
    assert "    if: github.event_name == 'workflow_dispatch'" in reconcile_job
    assert "    if: github.event_name != 'push'" not in deploy_job
    # A manual full run is prohibited until both independent acceptances
    # succeeded at exactly the same deployed main SHA.
    assert "Require current main SHA and successful manual P1/P11 acceptance" in deploy_job
    assert 'if [ "${GITHUB_REF}" != "refs/heads/main" ]; then' in deploy_job
    assert 'current="$(gh api "repos/$GITHUB_REPOSITORY/branches/main"' in deploy_job
    assert '--event workflow_dispatch --limit 50' in deploy_job
    assert 'p1-data-quality.yml p11-production-acceptance.yml' in deploy_job
    assert 'row.get("headSha")==expected' in deploy_job
    assert 'row.get("conclusion")=="success"' in deploy_job
    assert 'row.get("status")=="completed"' in deploy_job
    assert "Missing successful manually dispatched exact-SHA acceptance" in deploy_job
