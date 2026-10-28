"""Plan (never execute) safe REG.RU S3 immutable-map garbage collection.

Input: complete S3 ListObjectsV2 inventory JSON and a local JSON file for EVERY
datasets/<version>/manifest.json present in that inventory. Fails closed when
inventory or manifests are incomplete. Does NOT send requests or delete data.
"""
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

IMMUTABLE_PREFIXES = ("bundles/v1/", "indexes/v1/")
VERSION = re.compile(r"^datasets/([a-zA-Z0-9._-]+)/manifest\.json$")
LAYOUT = "regional-bundles-v1"


def _when(value: str) -> datetime:
    stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        raise ValueError("Inventory LastModified requires timezone")
    return stamp.astimezone(timezone.utc)


def plan_orphans(
    inventory: dict,
    manifest_payloads: dict[str, dict],
    *,
    now: datetime,
    minimum_age_days: int = 30,
) -> dict:
    """Returns a read-only candidate report; no deletions or API calls."""
    if minimum_age_days < 7:
        raise ValueError("minimum_age_days must be >= 7")
    if not isinstance(inventory.get("Contents"), list):
        raise ValueError("Complete inventory with Contents list required")
    if inventory.get("IsTruncated") is True or inventory.get("NextContinuationToken"):
        raise ValueError("S3 listing truncated: refusing to plan deletions")
    objects = inventory["Contents"]
    keys: set[str] = set()
    manifest_keys: set[str] = set()
    for item in objects:
        if not isinstance(item, dict) or not isinstance(item.get("Key"), str):
            raise ValueError("Malformed S3 inventory entry")
        key = item["Key"]
        if key in keys:
            raise ValueError(f"Duplicate inventory key: {key}")
        keys.add(key)
        if VERSION.fullmatch(key):
            manifest_keys.add(key)

    if not manifest_keys:
        raise ValueError("No dataset manifests in inventory: refusing to guess")
    if set(manifest_payloads) != manifest_keys:
        raise ValueError(
            f"Incomplete manifest evidence: expected {len(manifest_keys)}, got "
            f"{len(set(manifest_payloads) & manifest_keys)}; refusing to plan deletions"
        )

    referenced: set[str] = set()
    for path in sorted(manifest_keys):
        match = VERSION.fullmatch(path)
        assert match
        value = manifest_payloads[path]
        if value.get("version") != match.group(1) or value.get("layout") != LAYOUT:
            raise ValueError(f"Unexpected manifest version/layout for {path}")
        bundles = value.get("bundle_objects")
        indexes = value.get("index_shards")
        if not isinstance(bundles, list) or not isinstance(indexes, dict):
            raise ValueError(f"Missing bundle/index references in {path}")
        bundle_keys = [v for v in bundles if isinstance(v, str)]
        index_keys = [v for v in indexes.values() if isinstance(v, str)]
        if len(bundle_keys) != len(bundles) or len(index_keys) != len(indexes):
            raise ValueError(f"Malformed references in {path}")
        for key in bundle_keys + index_keys:
            if not key.startswith(IMMUTABLE_PREFIXES):
                raise ValueError(f"Unexpected referenced object key in {path}: {key}")
            if key not in keys:
                raise ValueError(f"Referenced object absent from inventory: {key}")
            referenced.add(key)

    cutoff = now.astimezone(timezone.utc) - timedelta(days=minimum_age_days)
    candidates = []
    kept_recent = 0
    kept_referenced = 0
    for item in objects:
        key = item["Key"]
        if not key.startswith(IMMUTABLE_PREFIXES):
            continue  # Never target dataset manifests, legacy layouts or other files
        if key in referenced:
            kept_referenced += 1
            continue
        if _when(str(item.get("LastModified", ""))) > cutoff:
            kept_recent += 1
            continue
        size = item.get("Size")
        if not isinstance(size, int) or size < 0:
            raise ValueError(f"Invalid object size for {key}")
        candidates.append({"key": key, "size_bytes": size})
    candidates.sort(key=lambda entry: entry["key"])
    return {
        "dry_run": True,
        "delete_operations_performed": 0,
        "manifest_count": len(manifest_keys),
        "s3_object_count": len(objects),
        "referenced_immutable_count": len(referenced),
        "recent_unreferenced_preserved": kept_recent,
        "referenced_preserved": kept_referenced,
        "minimum_age_days": minimum_age_days,
        "candidate_count": len(candidates),
        "candidate_bytes": sum(x["size_bytes"] for x in candidates),
        "candidates": candidates,
        "warning": "Plan only. No files can be deleted until independently reviewed.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--manifests", type=Path, required=True, help="Directory containing <version>.json")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--min-age-days", type=int, default=30)
    args = parser.parse_args()
    inventory = json.loads(args.inventory.read_text(encoding="utf-8-sig"))
    manifests = {}
    for item in inventory.get("Contents", []):
        key = item.get("Key") if isinstance(item, dict) else None
        match = VERSION.fullmatch(key or "")
        if match:
            local_path = args.manifests / f"{match.group(1)}.json"
            if not local_path.is_file():
                raise ValueError(f"Missing manifest: {local_path}")
            manifests[key] = json.loads(local_path.read_text(encoding="utf-8-sig"))
    report = plan_orphans(
        inventory, manifests,
        now=datetime.now(timezone.utc),
        minimum_age_days=args.min_age_days,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        f"Read-only S3 candidates={report['candidate_count']} "
        f"bytes={report['candidate_bytes']} report={args.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
