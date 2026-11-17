import hashlib
import importlib.util
import json
from pathlib import Path
import sys

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location("bounded_s3", SCRIPTS / "apply_reg_s3_orphans.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def fixture():
    manifest = {"version": "test", "layout": "regional-bundles-v1", "bundle_objects": ["bundles/v1/live.json"], "index_shards": {}}
    manifests = {"datasets/test/manifest.json": manifest}
    inventory = {"IsTruncated": False, "Contents": [
        {"Key": "bundles/v1/old.json", "Size": 10, "ETag": "old-etag", "LastModified": "2020-01-01T00:00:00Z"},
        {"Key": "bundles/v1/live.json", "Size": 10, "ETag": "live-etag", "LastModified": "2020-01-01T00:00:00Z"},
        {"Key": "datasets/test/manifest.json", "Size": 10},
    ]}
    plan = {"active_dataset": "test", "minimum_age_hours": 24,
            "candidate_objects": [{"key": "bundles/v1/old.json", "size_bytes": 10, "etag": "old-etag"}],
            "protected_manifests": {key: hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
                                    for key, value in manifests.items()}}
    return plan, inventory, manifests


def test_cleanup_accepts_only_old_unreferenced_unchanged_selection():
    plan, inventory, manifests = fixture()
    assert module.validate_selection(plan, inventory, manifests) == ["bundles/v1/old.json"]
    plan["candidate_objects"][0].update(key="bundles/v1/live.json", etag="live-etag")
    with pytest.raises(ValueError, match="no longer an orphan"):
        module.validate_selection(plan, inventory, manifests)


@pytest.mark.parametrize("change", ["etag", "manifest", "incomplete", "recent"])
def test_changed_or_incomplete_live_state_refuses_deletion(change):
    plan, inventory, manifests = fixture()
    if change == "etag":
        inventory["Contents"][0]["ETag"] = "replacement"
    elif change == "manifest":
        manifests["datasets/test/manifest.json"]["bundle_objects"].append("bundles/v1/old.json")
    elif change == "incomplete":
        inventory["IsTruncated"] = True
    else:
        inventory["Contents"][0]["LastModified"] = "2099-01-01T00:00:00Z"
    with pytest.raises(ValueError):
        module.validate_selection(plan, inventory, manifests)


def test_partial_delete_receipt_is_never_reported_as_success():
    client = object.__new__(module.Client)
    client.request = lambda *args, **kwargs: type("Response", (), {"content": b"<DeleteResult><Error><Key>one</Key></Error></DeleteResult>"})()
    with pytest.raises(RuntimeError, match="deletion errors"):
        client.delete_exact(["one"])
