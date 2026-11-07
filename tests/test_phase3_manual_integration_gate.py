"""Guard against accidental production ingestion when merging an implementation PR.

Temporary safety assertion; restoring automated full runs requires a separately
reviewed change and explicit operational approval.
"""
from pathlib import Path


def test_phase3_full_reconcile_requires_manual_dispatch_during_integration() -> None:
    workflow = (
        Path(__file__).resolve().parents[1]
        / ".github"
        / "workflows"
        / "phase3-lite-full-reconcile.yml"
    ).read_text(encoding="utf-8")
    triggers = workflow.split("\non:\n", 1)[1].split("\npermissions:", 1)[0]
    assert "  workflow_dispatch:" in triggers
    assert "  push:" not in triggers
    assert "  schedule:" not in triggers
