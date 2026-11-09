#!/usr/bin/env python3
"""ALFALOT browser read-only contract probe. Run ONLY against public UI.

No database writes, scheduler changes or reconciliation. Produces an aggregate
sanitized JSON summary: never saves cookies, response bodies or PII. Failed UI
or HTTP assertions remain failures, never synthetic completeness evidence.

python scripts/probe-alfalot-readonly.py --output alfalot-private-report.json --pager-selector '<verified CSS>'
"""
from __future__ import annotations

import argparse
import hashlib
import re
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, parse_qsl, urlsplit

from playwright.sync_api import sync_playwright

from bankrotai.connectors.etp_sources.alfalot_audit import (
    AlfalotContractError,
    PageProof,
    extract_detail_fields,
    extract_lot_references,
    terminal_controls_confirmed,
    verify_gd_filter_state,
    verify_two_walks,
)

URL = "https://etp.alfalot.ru/app/SearchLots/page"


_SENSITIVE_KEY = re.compile(r"token|auth|session|cookie|secret|key|password|email|phone|csrf|signature", re.I)
_SAFE_FILTER = re.compile(r"(?:stateFilter|lotFilter|pageNum|pageSize|offset|perPage)", re.I)
_SAFE_VALUE = re.compile(r"[A-Za-zА-Яа-я0-9._:-]{1,64}$")


def _allowed_fields(pairs) -> dict[str, list[str]]:
    """Persist only approved public filter/page parameter values, not PII."""
    result: dict[str, list[str]] = {}
    for name, value in pairs:
        if _SENSITIVE_KEY.search(name) or not _SAFE_FILTER.search(name):
            continue
        if not _SAFE_VALUE.fullmatch(value):
            continue
        if len(result) >= 50:
            break
        result.setdefault(name[:80], []).append(value)
    return result


def _safe_request(req, stage: str) -> dict | None:
    parsed = urlsplit(req.url)
    if parsed.hostname != "etp.alfalot.ru" or parsed.scheme != "https":
        return None
    if parsed.path.startswith(("/app/Login", "/app/Auth")):
        return None
    content_type = req.headers.get("content-type", "").split(";", 1)[0].lower()
    raw_body = req.post_data or ""
    body_fields: list[tuple[str, str]] = []
    if content_type == "application/x-www-form-urlencoded" and len(raw_body) < 1_000_000:
        body_fields = parse_qsl(raw_body, keep_blank_values=True)
    elif content_type == "application/json" and len(raw_body) < 1_000_000:
        try:
            data = json.loads(raw_body)
            if isinstance(data, dict):
                body_fields = [
                    (str(k), str(v)) for k, v in data.items()
                    if isinstance(v, (str, int, float, bool))
                ]
        except (ValueError, TypeError):
            pass
    # No raw body, query, cookies, session ID or user-specific fields.
    return {
        "stage": stage,
        "method": req.method,
        "path": parsed.path.split(";", 1)[0],
        "query_keys": sorted(
            k for k in parse_qs(parsed.query)
            if not _SENSITIVE_KEY.search(k)
        )[:60],
        "public_query_fields": _allowed_fields(parse_qsl(parsed.query)),
        "public_body_fields": _allowed_fields(body_fields),
        "body_keys": sorted(
            k for k, _ in body_fields if not _SENSITIVE_KEY.search(k)
        )[:60],
        "body_sha256": hashlib.sha256(raw_body.encode("utf-8")).hexdigest()
            if raw_body else None,
        "body_bytes": len(raw_body.encode("utf-8")) if raw_body else 0,
        "content_type": content_type,
        "resource_type": req.resource_type,
    }


def _safe_response(resp, stage: str) -> dict | None:
    parsed = urlsplit(resp.url)
    if parsed.hostname != "etp.alfalot.ru" or parsed.scheme != "https":
        return None
    return {
        "stage": stage, "method": resp.request.method,
        "path": parsed.path.split(";", 1)[0],
        "http_status": resp.status,
        "content_type": resp.headers.get("content-type", "").split(";", 1)[0],
    }


def _await_gd_filter(page, *, timeout_ms: int = 30000) -> None:
    """The GWT/JS catalogue renders after DOMContentLoaded, not with it."""
    try:
        page.wait_for_selector("#stateFilter_GD", state="attached", timeout=timeout_ms)
    except Exception as exc:
        try:
            text = page.locator("body").inner_text(timeout=1500).lower()
        except Exception:
            text = ""
        if "версия вашего браузера не поддерживается" in text:
            raise AlfalotContractError("provider returned unsupported-browser UI") from exc
        raise AlfalotContractError("GD filter absent after bounded GWT render wait") from exc


def _filter_selected(page) -> tuple[str, bool]:
    node = page.locator("#stateFilter_GD")
    if node.count() != 1:
        raise AlfalotContractError("expected one GD filter")
    label = node.inner_text(timeout=3000).strip()
    cls = str(node.get_attribute("class") or "")
    return label, ("selected" in cls.split())


def _discover_paginator_controls(page) -> list[dict]:
    """Diagnostic labels/classes only: no HTML dumps or personal data."""
    return page.locator("a, button, span, b").evaluate_all(
        """nodes => nodes
          .filter(el => /^(1|2|3|>|>>|‹|›|«|»)$/.test((el.innerText || '').trim()))
          .filter(el => {const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0})
          .slice(0, 100)
          .map(el => {
            const p = el.parentElement;
            return {
              element: el.tagName.toLowerCase(),
              label: (el.innerText || '').trim(),
              element_classes: Array.from(el.classList).slice(0, 5),
              parent_tag: p ? p.tagName.toLowerCase() : '',
              parent_classes: p ? Array.from(p.classList).slice(0, 5) : [],
              ancestor_classes: p && p.parentElement
                ? Array.from(p.parentElement.classList).slice(0, 5) : [],
              disabled: el.matches('[disabled], [aria-disabled=true], .disabled')
            };
          })"""
    )


def _select_pager(page, expected: int, selector: str):
    """Page proof must refer to an explicit paginator, never arbitrary page text."""
    candidates = page.locator(selector)
    if candidates.count() != 1:
        raise AlfalotContractError("exactly one paginator required; pass --pager-selector")
    pager = candidates.first
    active = [
        int(t.strip()) for t in pager.locator("b").all_inner_texts()
        if t.strip().isdecimal()
    ]
    if active != [expected]:
        raise AlfalotContractError(
            f"paginator active page does not match expected {expected}"
        )
    return pager


def _terminal_evidence(pager) -> bool:
    clickable = pager.locator("a, button:not([disabled])").all_inner_texts()
    disabled = pager.locator(
        ".disabled, [disabled], [aria-disabled='true']"
    ).all_inner_texts()
    return terminal_controls_confirmed(clickable, disabled)


def _advance(page, pager, expected: int, previous_ids: tuple[str, ...], selector: str) -> bool:
    """Click only an explicit next-number link *inside* the verified paginator."""
    next_nodes = pager.locator("a, button").filter(has_text=re.compile(
        r"^\s*" + re.escape(str(expected)) + r"\s*$"
    ))
    if next_nodes.count() > 1:
        raise AlfalotContractError("ambiguous paginator next-page control")
    if next_nodes.count() == 0:
        if not _terminal_evidence(pager):
            raise AlfalotContractError("missing next link is not terminal proof")
        return False
    node = next_nodes.first
    if not node.is_visible() or not node.is_enabled():
        raise AlfalotContractError("next-page control is unavailable")
    node.click(timeout=12000)
    page.wait_for_timeout(1600)
    _select_pager(page, expected, selector)
    after = tuple(r.entity for r in extract_lot_references(page.content()))
    if not after or after == previous_ids or set(after) == set(previous_ids):
        raise AlfalotContractError("page changed without distinct individual lot IDs")
    return True


def _walk(page, *, max_pages: int, pager_selector: str, stage: dict[str, str], walk_number: int):
    stage["value"] = f"walk_{walk_number}_landing"
    page.goto(URL, wait_until="domcontentloaded", timeout=45000)
    _await_gd_filter(page)
    node = page.locator("#stateFilter_GD")
    if node.count() != 1:
        raise AlfalotContractError("GD filter unavailable")
    label, selected = _filter_selected(page)
    if not selected:
        stage["value"] = f"walk_{walk_number}_GD_filter"
        node.click(timeout=10000)  # public read-only catalogue filtering
        page.wait_for_timeout(1500)
    label, selected = _filter_selected(page)
    verify_gd_filter_state(label, selected=selected)
    proofs: list[PageProof] = []
    observations: dict[str, object] = {}
    records: list[str] = []
    for page_num in range(1, max_pages + 1):
        pager = _select_pager(page, page_num, pager_selector)
        now_label, now_selected = _filter_selected(page)
        verify_gd_filter_state(now_label, selected=now_selected)
        if now_label != label:
            raise AlfalotContractError("GD state label changed during pagination")
        references = extract_lot_references(page.content())
        if not references:
            raise AlfalotContractError(f"page {page_num}: no individual lot links")
        ids = tuple(r.entity for r in references)
        for reference in references:
            if reference.entity in observations:
                raise AlfalotContractError("duplicate lot across pages")
            observations[reference.entity] = reference
            records.append(reference.entity + "|" + reference.row_text)
        stage["value"] = f"walk_{walk_number}_page_{page_num}_next"
        moved = _advance(page, pager, page_num + 1, ids, pager_selector)
        proofs.append(PageProof(
            page=page_num, state_id="stateFilter_GD", state_label=label,
            selected=selected, entity_ids=ids, next_page_control=moved,
            terminal_control=not moved,
        ))
        if not moved:
            digest = hashlib.sha256("\n".join(sorted(records)).encode("utf-8")).hexdigest()
            return proofs, observations, digest
    raise AlfalotContractError("max_pages reached; no verified terminal page")



def run(output: Path, max_pages: int, max_details: int, pager_selector: str | None, discover_only: bool) -> int:
    summary: dict = {
        "source": "alfalot.ru",
        "run_at_utc": datetime.now(timezone.utc).isoformat(),
        "production_enabled": False,
        "complete_source_inventory": False,
        "property_scope_verified": False,
        "reconcile_missing": False,
        "archival_allowed": False,
        "raw_http_contract_verified": False,
        "status": "NOT_VERIFIED",
    }
    requests: list[dict] = []
    responses: list[dict] = []
    stage: dict[str, str] = {"value": "initial"}
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.on("request", lambda req: requests.append(entry)
                        if (entry := _safe_request(req, stage["value"])) else None)
                page.on("response", lambda resp: responses.append(entry)
                        if (entry := _safe_response(resp, stage["value"])) else None)
                if discover_only:
                    stage["value"] = "discover_landing"
                    page.goto(URL, wait_until="domcontentloaded", timeout=45000)
                    _await_gd_filter(page)
                    node = page.locator("#stateFilter_GD")
                    if node.count() != 1:
                        raise AlfalotContractError("GD filter unavailable for discovery")
                    label, selected = _filter_selected(page)
                    if not selected:
                        stage["value"] = "discover_GD_filter_click"
                        node.click(timeout=10000)
                        page.wait_for_timeout(1600)
                    label, selected = _filter_selected(page)
                    summary["gd_label"] = label
                    summary["gd_selected"] = selected
                    summary["paginator_control_shapes"] = _discover_paginator_controls(page)
                    summary["first_page_lot_link_count"] = len(extract_lot_references(page.content()))
                    summary["status"] = "DIAGNOSTIC_ONLY_NOT_VERIFIED"
                    verify_gd_filter_state(label, selected=selected)
                else:
                    if not pager_selector:
                        raise AlfalotContractError("source-verified --pager-selector required")
                    first, references, first_digest = _walk(
                        page, max_pages=max_pages, pager_selector=pager_selector,
                        stage=stage, walk_number=1,
                    )
                    second, _, second_digest = _walk(
                        page, max_pages=max_pages, pager_selector=pager_selector,
                        stage=stage, walk_number=2,
                    )
                    summary.update(verify_two_walks(first, second))
                    if first_digest != second_digest:
                        raise AlfalotContractError("two catalogue field digests differ")
                    summary["catalogue_digest"] = first_digest
                    # Each detail is an individual LotCard. Opaque entity and
                    # displayed lot number are distinct; don't conflate them.
                    opened = 0
                    valid_price = 0
                    valid_status = 0
                    identity_conflicts = 0
                    for ref in list(references.values())[:max_details]:
                        stage["value"] = "individual_detail"
                        response = page.goto(
                            ref.detail_url, wait_until="domcontentloaded", timeout=30000
                        )
                        if response is None or response.status != 200:
                            continue
                        opened += 1
                        fields = extract_detail_fields(page.content(), ref.detail_url)
                        if fields["entity"] != ref.entity or (
                            ref.visible_lot_number and fields["visible_lot_number"]
                            and ref.visible_lot_number != fields["visible_lot_number"]
                        ):
                            identity_conflicts += 1
                            continue
                        valid_price += fields["start_price_kopecks"] is not None
                        valid_status += fields["status"] is not None
                    summary["individual_detail_pages_opened"] = opened
                    summary["detail_identity_conflicts"] = identity_conflicts
                    summary["initial_price_verified_count"] = valid_price
                    summary["status_verified_count"] = valid_status
                    summary["initial_price_success_rate"] = (
                        valid_price / opened if opened else None
                    )
                    summary["status_success_rate"] = (
                        valid_status / opened if opened else None
                    )
                    if identity_conflicts:
                        raise AlfalotContractError("individual detail identity mismatch")
                    summary["status"] = "PARTIAL_BROWSER_EVIDENCE_ONLY"
            finally:
                browser.close()
    except Exception as exc:
        summary["status"] = "BLOCKED_FAIL_CLOSED"
        summary["error_type"] = type(exc).__name__
        summary["error"] = str(exc)[:180]
    # Deduplicated request shapes, not raw URLs/bodies/headers. No cookies or
    # tokens. These are diagnostic traces, NOT full raw HTTP contract evidence.
    summary["observed_request_shapes"] = list({
        json.dumps(entry, sort_keys=True): entry for entry in requests
    }.values())[:250]
    summary["observed_response_shapes"] = list({
        json.dumps(entry, sort_keys=True): entry for entry in responses
    }.values())[:250]
    summary["raw_http_contract_verified"] = False  # requires independent semantic review
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if summary["status"] == "PARTIAL_BROWSER_EVIDENCE_ONLY" else 2


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-pages", type=int, default=30)
    parser.add_argument("--max-details", type=int, default=30)
    parser.add_argument(
        "--pager-selector",
        help="Source-verified CSS selector for the single paginator (not an arbitrary page link)",
    )
    parser.add_argument(
        "--discover-only", action="store_true",
        help="Collect sanitized UI/network control shapes; never claims a full contract",
    )
    args = parser.parse_args()
    if not (1 <= args.max_pages <= 100 and 0 <= args.max_details <= 100):
        parser.error("max-pages 1..100, max-details 0..100")
    return run(args.output, args.max_pages, args.max_details, args.pager_selector, args.discover_only)


if __name__ == "__main__":
    raise SystemExit(main())
