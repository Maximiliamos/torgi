from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "p9-geo-quality-canary.yml"


def test_p9_canary_workflow_is_manual_and_bounded() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "workflow_dispatch:" in text
    assert "schedule:" not in text
    assert "branches: [main]" not in text
    assert "P9 canary limit must be between 1 and 500" in text
    assert "geo_quality_canary_plan" in text
    assert "requeue_geo_quality_canary" in text
    assert "bankrotai-home-geocoding-worker" in text


def test_p9_canary_does_not_activate_tbankrot_or_reconcile_sources() -> None:
    text = WORKFLOW.read_text(encoding="utf-8").casefold()

    assert "tbankrot" not in text
    assert "reconcile_missing" not in text
    assert "nationwide_lot_sync" not in text
