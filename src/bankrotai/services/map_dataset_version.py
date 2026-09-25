from __future__ import annotations

import re


# Bump this token whenever map membership or public payload semantics change.
# Production deployment requires the current dataset to carry this revision,
# so a dataset produced by older map code cannot silently remain active.
MAP_DATASET_REVISION = "r3"

_REVISION_PATTERN = re.compile(r"-(r\d+)(?=(?:-bundle)?-s3$|$)")


def dataset_pipeline_revision(version: str) -> str | None:
    match = _REVISION_PATTERN.search(str(version or ""))
    return match.group(1) if match else None


def build_map_dataset_version(
    timestamp: str,
    *,
    object_store_enabled: bool,
    object_store_layout: str,
) -> str:
    base = f"{timestamp}-{MAP_DATASET_REVISION}"
    if not object_store_enabled:
        return base
    if object_store_layout == "regional-bundles-v1":
        return f"{base}-bundle-s3"
    return f"{base}-s3"


def dataset_matches_current_pipeline(
    version: str,
    *,
    object_store_enabled: bool,
    object_store_layout: str,
) -> bool:
    if dataset_pipeline_revision(version) != MAP_DATASET_REVISION:
        return False
    if not object_store_enabled:
        return str(version).endswith(f"-{MAP_DATASET_REVISION}")
    if object_store_layout == "regional-bundles-v1":
        return str(version).endswith(f"-{MAP_DATASET_REVISION}-bundle-s3")
    return (
        str(version).endswith(f"-{MAP_DATASET_REVISION}-s3")
        and not str(version).endswith("-bundle-s3")
    )
