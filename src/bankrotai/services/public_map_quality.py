"""Independent pre-publication public-map contamination metrics (P17).

Pure function; does not trust SQL filtering or mutate a DB. The MapDataset
publisher refuses promotion if any critical metric is nonzero.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from bankrotai.services.public_map_policy import (
    PUBLIC_ACTIVE_STATUSES, TRUSTED_CADASTRAL_GEO_SOURCES,
)
from bankrotai.services.real_estate_filter import REAL_ESTATE_CATEGORIES

RENT_TOKENS = ("аренд", "субаренд", "договор найма", "право пользования")
MOVABLE_TOKENS = ("мопед", "мотоцикл", "автомобил", "транспортн", "прицеп", "грузовик", "спецтехник", "лодк", "катер")
_METRICS = (
    "public_rental_count", "public_transport_count", "public_closed_count",
    "public_movable_count", "stale_source_only_count",
    "cadastral_address_fallback_count", "low_quality_geo_count",
    "locality_mismatch_count",
)
CRITICAL_METRICS = (
    "public_rental_count", "public_transport_count", "public_closed_count",
    "public_movable_count", "stale_source_only_count",
    "cadastral_address_fallback_count",
)


def public_map_preflight(points: list[dict[str, Any]]) -> dict[str, Any]:
    counts: Counter[str] = Counter()
    for item in points:
        title = str(item.get("title") or "").casefold().replace("ё", "е")
        category = str(item.get("category") or "")
        source = str(item.get("source_system") or "")
        geo_source = str(item.get("geo_source") or "")
        cadastral = item.get("cadastral_number")
        if any(term in title for term in RENT_TOKENS):
            counts["public_rental_count"] += 1
        if item.get("vin") or any(term in title for term in MOVABLE_TOKENS):
            counts["public_transport_count"] += 1
        if item.get("status") not in PUBLIC_ACTIVE_STATUSES or item.get("is_archived"):
            counts["public_closed_count"] += 1
        if category not in REAL_ESTATE_CATEGORIES:
            counts["public_movable_count"] += 1
        if source != "test" and not item.get("independent_source_verified"):
            counts["stale_source_only_count"] += 1
        if cadastral and geo_source not in TRUSTED_CADASTRAL_GEO_SOURCES:
            counts["cadastral_address_fallback_count"] += 1
        if item.get("geo_confidence") in ("low", "none", "unknown") or item.get("needs_geo_check"):
            counts["low_quality_geo_count"] += 1
        if item.get("locality_mismatch"):
            counts["locality_mismatch_count"] += 1
    metrics = {name: counts[name] for name in _METRICS}
    return {
        "ok": all(metrics[name] == 0 for name in CRITICAL_METRICS),
        "point_count": len(points),
        **metrics,
    }
