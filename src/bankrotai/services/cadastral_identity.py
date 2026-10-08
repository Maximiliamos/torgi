"""Pure provider identity selection. Never trust a cadastral search result's list order."""

from __future__ import annotations

import re
from typing import Any

CADASTRAL_RE = re.compile(r"^\d{2}:\d{2}:\d{6,7}:\d+$")


def pick_pkk_feature(features: list[dict], query: str) -> dict | None:
    expected = query.replace(" ", "")
    for feature in features:
        if not isinstance(feature, dict):
            continue
        attrs = feature.get("attrs") or {}
        if not isinstance(attrs, dict):
            continue
        for key in ("cn", "cad_num", "cadastralNumber", "cadastral_number"):
            if str(attrs.get(key) or "").replace(" ", "") == expected:
                return feature
    return None


def pick_nspd_feature(features: list[dict], query: str) -> dict | None:
    expected = query.replace(" ", "")
    if not CADASTRAL_RE.fullmatch(expected):
        return next((item for item in features if isinstance(item, dict)), None)
    for feature in features:
        if not isinstance(feature, dict):
            continue
        properties = feature.get("properties") or {}
        if not isinstance(properties, dict):
            continue
        for source in (properties.get("options") or {}, properties):
            if not isinstance(source, dict):
                continue
            for key in ("cad_num", "cadNum", "cadastralNumber", "cadastral_number", "cn"):
                if str(source.get(key) or "").replace(" ", "") == expected:
                    return feature
    return None


def exact_cadastral_geo_verified(
    expected_number: str | None,
    alternate_numbers: list[str] | None,
    observed_number: str | None,
    provider: str | None,
) -> bool:
    """Weak address, unmatched cadastre and point-based WMS are only hints."""
    observed = str(observed_number or "").replace(" ", "")
    candidates = {
        str(value).replace(" ", "")
        for value in (expected_number, *(alternate_numbers or []))
        if value
    }
    return bool(
        observed and observed in candidates
        and provider in {"nspd", "ik12_cadastral", "pkk"}
    )
