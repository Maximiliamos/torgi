"""Offline gates for the read-only seven-source post-run evidence extractor."""
from __future__ import annotations

import ast
from pathlib import Path
import runpy

ROOT = Path(__file__).resolve().parents[1]
AUDIT = ROOT / "scripts" / "audit_20261108_nationwide_readonly.py"


def test_audit_source_compiles_and_never_commits() -> None:
    source = AUDIT.read_text(encoding="utf-8")
    ast.parse(source, filename=str(AUDIT))
    tree = ast.parse(source)
    calls = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    ]
    forbidden = {"commit", "flush", "delete", "add", "bulk_insert_mappings", "apply_async"}
    assert not any(node.func.attr in forbidden for node in calls)
    assert "session.scalar" not in source or "select(MapDataset)" in source
    assert "run.started_at" in source and "run_finished" in source
    assert "attribution" in source and "time-window only" in source


def test_checkpoint_extractor_accepts_only_explicit_aggregate_keys() -> None:
    module = runpy.run_path(str(AUDIT), run_name="read_only_import")
    metrics = module["checkpoint_metrics"]({
        "detail_attempts": 18,
        "items_rejected": 41,
        "private_source_cookie": "SECRET",
        "quarantine_reasons": {"bad_region": 1},
    })
    assert metrics == {
        "detail_attempts": 18,
        "items_rejected": 41,
        "quarantine_reasons": {"bad_region": 1},
    }
    assert module["checkpoint_metrics"]("not-json") == {}


def test_all_seven_sources_and_archive_categories_are_preserved() -> None:
    module = runpy.run_path(str(AUDIT), run_name="read_only_import")
    assert set(module["KNOWN_SOURCES"]) == {
        "torgi.gov.ru", "lot-online.ru", "torgi-russia.ru", "bidexpert.ru",
        "m-ets.ru", "cdtrf.ru", "nistp.ru",
    }
    assert module["DEFAULT_RUN_ID"] == "f572bdff-3db1-41bf-94a4-1f75ab690232"


def test_readonly_archive_samples_are_bounded_and_geo_absence_is_explicit() -> None:
    source = AUDIT.read_text(encoding="utf-8")
    assert ".limit(20)" in source
    assert '"sample_recent_archives"' in source
    assert '"missing_successful_runs"' in source
    assert '"last_sync_run_id"' in source
    assert '"new_source_rows_with_coordinates_now"' in source
    assert '"new_source_rows_without_coordinates_now"' in source
    assert '"new_source_rows_with_coordinates_but_not_map_eligible"' in source
    ast.parse(source)
