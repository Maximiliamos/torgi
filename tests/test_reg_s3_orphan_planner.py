"""Garbage collection planner must never propose referenced or recent objects."""
from __future__ import annotations

import importlib.util
from datetime import datetime, timezone
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/plan_reg_s3_orphans.py"
SPEC = importlib.util.spec_from_file_location("s3_gc_planner", SCRIPT)
assert SPEC and SPEC.loader
P = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(P)

NOW = datetime(2026, 10, 28, tzinfo=timezone.utc)
OLD = "2026-09-01T12:00:00+00:00"
NEW = "2026-10-25T12:00:00+00:00"


def _fixture():
    v = "20261028T180900195004Z-r6-bundle-s3"
    manifest_key = f"datasets/{v}/manifest.json"
    objects = [
        {"Key": manifest_key, "Size": 2, "LastModified": NEW},
        {"Key": "bundles/v1/aa/live.json", "Size": 100, "LastModified": OLD},
        {"Key": "indexes/v1/bb/live.json", "Size": 110, "LastModified": OLD},
        {"Key": "bundles/v1/cc/old.json", "Size": 120, "LastModified": OLD},
        {"Key": "indexes/v1/dd/recent.json", "Size": 130, "LastModified": NEW},
        {"Key": "other/private.txt", "Size": 400, "LastModified": OLD},
    ]
    manifests = {
        manifest_key: {
            "version": v,
            "layout": "regional-bundles-v1",
            "bundle_objects": ["bundles/v1/aa/live.json"],
            "index_shards": {"zoom": "indexes/v1/bb/live.json"},
        },
    }
    return {"Contents": objects, "IsTruncated": False}, manifests


def test_planner_is_readonly_and_keeps_all_live_and_recent_data():
    inventory, manifests = _fixture()
    report = P.plan_orphans(inventory, manifests, now=NOW)
    assert report["delete_operations_performed"] == 0
    assert report["dry_run"] is True
    assert report["candidate_count"] == 1
    assert report["candidate_bytes"] == 120
    assert report["candidates"] == [
        {"key": "bundles/v1/cc/old.json", "size_bytes": 120}
    ]
    assert report["referenced_immutable_count"] == 2
    assert report["recent_unreferenced_preserved"] == 1


@pytest.mark.parametrize("problem", [
    "truncated", "no_manifest", "missing_manifest", "bad_layout", "missing_referenced",
])
def test_planner_fails_closed_for_incomplete_inventory(problem):
    inventory, manifests = _fixture()
    key = next(iter(manifests))
    if problem == "truncated":
        inventory["IsTruncated"] = True
    elif problem == "no_manifest":
        inventory["Contents"] = [x for x in inventory["Contents"] if x["Key"] != key]
        manifests = {}
    elif problem == "missing_manifest":
        manifests = {}
    elif problem == "bad_layout":
        manifests[key]["layout"] = "legacy"
    elif problem == "missing_referenced":
        inventory["Contents"] = [
            x for x in inventory["Contents"] if x["Key"] != "indexes/v1/bb/live.json"
        ]
    with pytest.raises(ValueError):
        P.plan_orphans(inventory, manifests, now=NOW)


def test_minimum_age_is_not_allowed_to_shrink_below_seven_days():
    inventory, manifests = _fixture()
    with pytest.raises(ValueError):
        P.plan_orphans(inventory, manifests, now=NOW, minimum_age_days=1)


@pytest.mark.parametrize("bad", [
    "unknown_truncation", "empty_next_token", "bad_size", "bad_modified",
])
def test_planner_requires_complete_and_valid_immutable_inventory(bad):
    inventory, manifests = _fixture()
    if bad == "unknown_truncation":
        del inventory["IsTruncated"]
    elif bad == "empty_next_token":
        inventory["NextContinuationToken"] = "next-hidden-page"
    elif bad == "bad_size":
        for item in inventory["Contents"]:
            if item["Key"] == "bundles/v1/aa/live.json":
                item["Size"] = "100"
    elif bad == "bad_modified":
        for item in inventory["Contents"]:
            if item["Key"] == "indexes/v1/bb/live.json":
                item["LastModified"] = "2026-09-01T12:00:00"
    with pytest.raises(ValueError):
        P.plan_orphans(inventory, manifests, now=NOW)


def test_planner_requires_aware_now_and_integer_age():
    inventory, manifests = _fixture()
    with pytest.raises(ValueError, match="timezone-aware"):
        P.plan_orphans(inventory, manifests, now=datetime(2026, 10, 30))
    with pytest.raises(ValueError, match="integer"):
        P.plan_orphans(inventory, manifests, now=NOW, minimum_age_days=True)
