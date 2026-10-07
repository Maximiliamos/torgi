from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "p1-data-quality.yml"


def test_p1_push_audit_waits_for_exact_home_revision() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "push:" in text
    assert "'src/bankrotai/services/quality.py'" in text
    assert "Wait for exact Home production revision" in text
    assert "Deploy home secondary origin" in text
    assert "head_sha=$GITHUB_SHA" in text
    assert "&event=push" not in text
    assert '"completed:success" if any' in text
    assert "needs: wait-home-deploy" in text


def test_p1_keeps_schedule_and_full_reconciliation_trigger() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "cron: '37 4 * * *'" in text
    assert "workflows: ['Phase 3 Lite full source reconciliation']" in text
    assert "cancel-in-progress: true" in text
