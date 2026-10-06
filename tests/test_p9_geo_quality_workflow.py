from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "p9-geo-quality-canary.yml"


def test_p9_canary_workflow_is_bounded_and_waits_for_exact_deploy() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "workflow_dispatch:" in text
    assert "schedule:" not in text
    assert "branches: [main]" in text
    assert "'.github/workflows/p9-geo-quality-canary.yml'" in text
    assert "Wait for exact Home production revision" in text
    assert 'head_sha=$GITHUB_SHA' in text
    assert "Deploy home secondary origin" in text
    assert "$limit = if ($pushRun) { 50 }" in text
    assert "P9 canary limit must be between 1 and 500" in text
    assert "geo_quality_canary_plan" in text
    assert "requeue_geo_quality_canary" in text
    assert "geocode_pending_lots" in text
    assert "lot_ids=target_ids" in text
    assert "allow_when_paused=True" in text
    assert "refresh_strategy=False" in text
    assert "geocode_pending_lots_task.apply_async" not in text
    assert "bankrotai-home-geocoding-worker" in text


def test_p9_canary_does_not_activate_tbankrot_or_reconcile_sources() -> None:
    text = WORKFLOW.read_text(encoding="utf-8").casefold()

    assert "tbankrot" not in text
    assert "reconcile_missing" not in text
    assert "nationwide_lot_sync" not in text
