from __future__ import annotations

from pathlib import Path

import pytest

from bankrotai.services.ingestion import source_full_specs


@pytest.mark.parametrize("source_id", ["rus-on.ru", "sistematorg.com"])
def test_reported_source_full_specs_are_fail_closed_for_archival(source_id: str) -> None:
    specs = source_full_specs(source_id)
    assert len(specs) == 1
    spec = specs[0]
    assert spec.source_id == source_id
    assert spec.reconcile_missing is False
    assert spec.require_source_end is True
    assert spec.max_batches == 3500


def test_reported_source_refresh_workflow_is_manual_and_single_source() -> None:
    text = Path(".github/workflows/reported-source-refresh.yml").read_text(encoding="utf-8")
    assert "workflow_dispatch:" in text
    assert "workflow_run:" not in text
    assert "automatic_nationwide_source_retry_task" not in text
    assert "schedule_nationwide_lot_sync" in text
    assert "rus-on.ru" in text
    assert "sistematorg.com" in text
    assert "active nationwide ingestion already owns the durable lease" in text
