from __future__ import annotations

from typing import Any

from bankrotai.connectors.etp_sources.source_specific import datetime_after
from bankrotai.connectors.etp_sources.status_normalization import _status
from bankrotai.domain import NormalizedLot

BANKRUPTCY_SOURCE = "Продажа имущества (предприятия) банкротов"
BANKRUPTCY_SECTION_ID = "13"
REAL_ESTATE_BRANCH = "Недвижимое имущество"


def parse_sberbank_json(profile: Any, payload: dict[str, Any], *, page: int) -> list[NormalizedLot]:
    # Imported lazily to keep common.py below the repository module-size guard
    # without introducing an import cycle at module initialization.
    from bankrotai.connectors.etp_sources.common import (
        SourceSchemaError,
        _CADASTRAL_RE,
        _VIN_RE,
        _category,
        _clean,
        _external_id,
        _safe_provider_url,
    )

    items = payload.get("PurchaseList")
    if not isinstance(items, list):
        raise SourceSchemaError(f"{profile.source_id}: PurchaseList is missing from API response")
    if not items:
        raise SourceSchemaError(
            f"{profile.source_id}: empty PurchaseList cannot prove that the bankruptcy filter was applied"
        )

    lots: list[NormalizedLot] = []
    seen: set[str] = set()
    for item in items:
        raw = item.get("_source") if isinstance(item, dict) else None
        if not isinstance(raw, dict):
            raise SourceSchemaError(f"{profile.source_id}: malformed PurchaseList row")

        object_source = _clean(str(raw.get("ObjectSource") or ""))
        section_id = _clean(str(raw.get("SourceTradeSectionId") or ""))
        if object_source != BANKRUPTCY_SOURCE or section_id != BANKRUPTCY_SECTION_ID:
            raise SourceSchemaError(
                f"{profile.source_id}: bankruptcy filter is not confirmed by the returned row"
            )

        if _clean(str(raw.get("BranchGroup") or "")) != REAL_ESTATE_BRANCH:
            continue

        href = str(raw.get("objectHref") or "")
        match = profile.lot_url_re.search(href)
        if not match:
            raise SourceSchemaError(
                f"{profile.source_id}: real-estate bankruptcy row has no verified GoodsView identity"
            )
        _safe_provider_url(profile, href)
        external_id = _external_id(profile, href, match)
        if external_id in seen:
            raise SourceSchemaError(f"{profile.source_id}: duplicate GoodsView identity on page {page}")
        seen.add(external_id)

        title = _clean(str(raw.get("title") or raw.get("BidName") or raw.get("purchName") or ""))
        if not title:
            raise SourceSchemaError(f"{profile.source_id}: verified GoodsView row has no title")

        price: float | None = None
        amount = raw.get("purchAmountRUB")
        currency = str(raw.get("purchCurrency") or raw.get("CurrencyCode") or "").strip().upper()
        if amount is None and currency in {"RUB", "643"}:
            amount = raw.get("purchAmount")
        try:
            if isinstance(amount, (str, int, float)) and not isinstance(amount, bool) and amount != "":
                candidate = float(str(amount).replace(" ", "").replace(",", "."))
                if 0 < candidate < 1e15:
                    price = candidate
        except (TypeError, ValueError, OverflowError):
            pass

        text_value = _clean(" ".join(
            str(raw.get(key) or "") for key in ("title", "purchName", "Address")
        ))
        cadastral = _CADASTRAL_RE.search(str(raw.get("cadastralNumber") or text_value))
        vin = _VIN_RE.search(text_value)
        identity_path = "/".join(match.groups())
        lots.append(NormalizedLot(
            external_id=external_id,
            source="sberbank-ast",
            source_system=profile.source_id,
            title=title[:500],
            description=text_value[:5000],
            category=_category(text_value),
            region_slug=None,
            region_name=_clean(str(raw.get("RegionNameTerm") or "")) or None,
            address=_clean(str(raw.get("Address") or "")) or None,
            cadastral_number=cadastral.group(1) if cadastral else None,
            vin=vin.group(0).upper() if vin else None,
            area=None,
            start_price=price,
            current_price=None,
            auction_status=_status(str(raw.get("PurchaseStageTerm") or raw.get("purchStateNameTerm") or "")),
            lot_url=href,
            source_url=href,
            detail_level="search",
            raw_data={
                "listing_page": page,
                "complete_source_inventory": False,
                "safe_for_archival": False,
                "reconcile_missing": False,
                "bankruptcy_filter_verified": True,
                "bankruptcy_source_term": object_source,
                "source_trade_section_id": section_id,
                "property_scope_verified": True,
                "property_branch_group": REAL_ESTATE_BRANCH,
                "goods_view_identity": identity_path,
                "individual_lot_identity_verified": True,
                "detail_contract": "GoodsView",
            },
            platform_name=profile.platform_name,
            platform_code=profile.platform_code,
            procedure_number=_clean(str(raw.get("purchCode") or "")) or None,
            application_deadline=datetime_after(
                f"Дата окончания представления заявок {raw.get('RequestStopDate') or ''}",
                r"дата окончания представления заявок",
            ),
            auction_at=None,
        ))
    return lots
