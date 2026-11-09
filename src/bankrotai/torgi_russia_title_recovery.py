"""Evidence-backed recovery for malformed Torgi Russia catalogue titles."""
from __future__ import annotations

import math
import json
from collections.abc import Callable
from hashlib import sha256
from typing import Any
from urllib.parse import urljoin


def classify_rejections(
    records: list,
    normalized_lots: list,
    recovery_failures: dict[str, str],
) -> tuple[dict[str, int], list[dict[str, str | None]]]:
    """Describe only rejected records without persisting their raw contents."""
    accepted_ids = {
        lot.external_id.rsplit(":", 1)[-1]
        for lot in normalized_lots
    }
    counts: dict[str, int] = {}
    samples: list[dict[str, str | None]] = []
    for item in records:
        if not isinstance(item, dict):
            reason = "record_not_object"
        else:
            raw_id = item.get("id")
            if isinstance(raw_id, bool) or not (
                isinstance(raw_id, int)
                or (isinstance(raw_id, str) and raw_id.isascii() and raw_id.isdigit())
            ):
                reason = f"invalid_id_type:{type(raw_id).__name__}"
            elif not str(item.get("title") or "").strip():
                reason = "empty_title"
            elif str(raw_id) in accepted_ids:
                continue
            else:
                reason = "unknown_parser_drop"
        counts[reason] = counts.get(reason, 0) + 1
        if len(samples) < 10:
            raw_id = item.get("id") if isinstance(item, dict) else None
            serialized = json.dumps(item, ensure_ascii=False, sort_keys=True, default=str)
            samples.append({
                "external_id": str(raw_id) if raw_id is not None else None,
                "reason": reason,
                "record_sha256": sha256(serialized.encode()).hexdigest(),
                "recovery_failure": recovery_failures.get(str(raw_id)),
            })
    return counts, samples


def recovery_failure_code(error: BaseException) -> str:
    """Return a stable, non-sensitive recovery failure code for diagnostics."""
    message = str(error).casefold()
    markers = (
        ("no canonical id", "invalid_id"),
        ("detail identity is absent or ambiguous", "torgi_detail_identity"),
        ("no unique canonical m-ets lot link", "mets_link_ambiguous"),
        ("expected an https m-ets lot url", "mets_link_invalid"),
        ("redirected public detail url", "mets_redirect"),
        ("redirect from public m-ets", "mets_redirect"),
        ("provider access explicitly refused", "mets_access_refused"),
        ("transient http failure", "mets_http_failure"),
        ("fallback price does not match", "mets_price_mismatch"),
        ("page sku contradicts url or is ambiguous", "mets_detail_contract"),
        ("no matching m-ets trade/lot identifier", "mets_detail_contract"),
        ("no trustworthy m-ets lot title", "mets_detail_contract"),
        ("html page exceeds safety limit", "mets_detail_contract"),
        ("expected html from m-ets", "mets_detail_contract"),
    )
    for marker, code in markers:
        if marker in message:
            return f"{type(error).__name__}:{code}"
    return type(error).__name__


def _canonical_mets_lot_url(detail_record: dict) -> str:
    """Return the one exact official M-ETS lot link asserted by the aggregator."""
    from bankrotai.connectors.registry.mets_public import _host_lot_url

    source_links = detail_record.get("source_links")
    candidates: list[str] = []
    if isinstance(source_links, list):
        for link in source_links:
            if not isinstance(link, dict):
                continue
            candidate = str(link.get("link") or "").strip()
            try:
                canonical, _trade, _lot = _host_lot_url(candidate)
            except ValueError:
                continue
            candidates.append(canonical)
    unique = list(dict.fromkeys(candidates))
    if len(unique) != 1:
        raise RuntimeError(
            "Torgi Russia empty-title detail has no unique canonical M-ETS lot link"
        )
    return unique[0]


def recover_empty_title(
    item: dict,
    *,
    base_url: str,
    get_with_retry: Callable[..., Any],
    extract_records: Callable[..., list[dict]],
    parse_price: Callable[[str], float | None],
) -> dict:
    """Recover a title only from the linked official ETP with ID and price proof."""
    raw_id = item.get("id")
    if isinstance(raw_id, bool) or not isinstance(raw_id, (int, str)):
        raise RuntimeError("Torgi Russia empty-title record has no canonical ID")
    numeric_id = str(raw_id)
    if not numeric_id.isascii() or not numeric_id.isdigit():
        raise RuntimeError("Torgi Russia empty-title record has no canonical ID")

    detail_url = urljoin(base_url, f"/lot/{numeric_id}")
    detail_response = get_with_retry(detail_url)
    detail_records = [
        record
        for record in extract_records(detail_response.text, require_linked=False)
        if str(record.get("id")) == numeric_id
    ]
    if len(detail_records) != 1:
        raise RuntimeError(
            "Torgi Russia empty-title detail identity is absent or ambiguous"
        )
    detail_record = detail_records[0]
    if str(detail_record.get("title") or "").strip():
        recovered = dict(item)
        recovered["title"] = str(detail_record["title"]).strip()
        recovered["title_evidence"] = "torgi_russia_detail"
        return recovered

    mets_url = _canonical_mets_lot_url(detail_record)
    from bankrotai.connectors.registry.mets_public import (
        detail_request_url,
        parse_mets_lot,
    )

    mets_request_url = detail_request_url(mets_url)
    mets_response = get_with_retry(mets_request_url)
    if getattr(mets_response, "history", []) or str(
        getattr(mets_response, "url", "")
    ) != mets_request_url:
        raise RuntimeError("Torgi Russia M-ETS fallback redirected public detail URL")

    mets_lot = parse_mets_lot(mets_response.text, mets_request_url)
    source_prices = [
        value
        for value in (mets_lot.start_price, mets_lot.current_price)
        if value is not None
    ]
    aggregator_prices = [
        parse_price(str(value))
        for value in (
            item.get("start_price"),
            item.get("current_price"),
            detail_record.get("start_price"),
            detail_record.get("current_price"),
        )
    ]
    price_matches = any(
        math.isclose(float(source_price), float(aggregator_price), abs_tol=0.01)
        for source_price in source_prices
        for aggregator_price in aggregator_prices
        if aggregator_price is not None
    )
    if not source_prices or not price_matches:
        raise RuntimeError(
            "Torgi Russia empty-title M-ETS fallback price does not match"
        )

    recovered = dict(item)
    recovered["title"] = mets_lot.title
    recovered["title_evidence"] = "official_mets_detail"
    recovered["title_evidence_url"] = mets_request_url
    recovered["title_evidence_canonical_url"] = mets_url
    recovered["title_evidence_external_id"] = mets_lot.external_id
    return recovered
