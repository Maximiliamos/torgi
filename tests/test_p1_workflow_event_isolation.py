"""Prevent failed source reconciliation events from cancelling active production P1."""

from pathlib import Path


WORKFLOW = (Path(__file__).resolve().parents[1] / ".github/workflows/p1-data-quality.yml").read_text(
    encoding="utf-8"
)


def test_p1_push_audit_is_not_cancelled_by_failed_reconciliation_event() -> None:
    assert "github.event.workflow_run.head_sha || github.sha" in WORKFLOW
    assert "${{ github.event_name }}" in WORKFLOW
    assert "github.event.workflow_run.conclusion || 'n/a'" in WORKFLOW
    assert "cancel-in-progress: true" in WORKFLOW


def test_failed_workflow_run_cannot_execute_production_audit() -> None:
    assert "github.event.workflow_run.conclusion == 'success'" in WORKFLOW
    assert "github.event.workflow_run.head_branch == 'main'" in WORKFLOW
    assert "github.event.workflow_run.head_sha == github.sha" in WORKFLOW
    assert "runs-on: [self-hosted, Windows, X64, bankrotai-home]" in WORKFLOW
