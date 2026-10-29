"""Strict first-party cadastral feature matching, independent of list ordering."""
from __future__ import annotations

from bankrotai.services.geo_cadastral_properties import (
    CADASTRAL_RE,
    normalize_nspd_props,
)


def pick_pkk_feature(features: list[dict], query: str) -> dict | None:
    expected = query.replace(" ", "")
    if not CADASTRAL_RE.fullmatch(expected):
        return None
    for feature in features:
        if not isinstance(feature, dict):
            continue
        attrs = feature.get("attrs") or {}
        if not isinstance(attrs, dict):
            continue
        for key in ("cn", "cad_num", "cadnum", "cadastralNumber", "cadastral_number"):
            observed = str(attrs.get(key) or "").replace(" ", "")
            if observed == expected:
                return feature
    return None


def pick_nspd_feature(features: list[dict], query: str) -> dict | None:
    expected = query.replace(" ", "")
    if not CADASTRAL_RE.fullmatch(expected):
        return None
    for feature in features:
        if not isinstance(feature, dict):
            continue
        props = feature.get("properties") or {}
        if not isinstance(props, dict):
            continue
        # No substring/free-text matching; never substitute requested ID.
        info = normalize_nspd_props(props, "")
        observed = str(info.get("Кадастровый номер") or "").replace(" ", "")
        if observed == expected:
            return feature
    return None
