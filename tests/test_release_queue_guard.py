from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
P1 = ROOT / ".github" / "workflows" / "p1-data-quality.yml"
RECOVERY = ROOT / ".github" / "workflows" / "recover-superseded-release-runs.yml"


def test_p1_superseded_reconciliation_cannot_cancel_current_release_audit() -> None:
    workflow = P1.read_text(encoding="utf-8")

    assert "p1-production-data-quality-${{ github.event.workflow_run.head_sha || github.sha }}" in workflow
    assert "github.event.workflow_run.head_sha == github.sha" in workflow
    assert "github.event.workflow_run.head_branch == 'main'" in workflow
    assert "github.event.workflow_run.conclusion == 'success'" in workflow
    assert "cancel-in-progress: true" in workflow
    assert "Wait for exact Home production revision" in workflow


def test_release_runner_cleanup_is_explicitly_bounded_and_fail_closed() -> None:
    workflow = RECOVERY.read_text(encoding="utf-8")

    assert "actions: write" in workflow
    assert "branches: [main]" in workflow
    assert "current_sha != current_workflow_sha" in workflow
    assert "exact_targets =" in workflow
    assert "run.get(\"head_sha\") != sha or run.get(\"name\") != name" in workflow
    assert "Wait for full reconciliation to finish" in workflow
    assert "finished.get(\"conclusion\") == \"success\"" in workflow
    assert "retained-fail-closed" in workflow
    assert "/actions/runs/{run_id}/force-cancel" in workflow
    assert "manual-cancel-required-http-" in workflow
    assert "No current-main workflow run is in the cancellation allowlist." in workflow
