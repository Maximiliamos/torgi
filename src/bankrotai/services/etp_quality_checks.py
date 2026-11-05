"""Pure, read-only checks for individual ETP lot identity and financial data.

All returned evidence is aggregate. Never emit lot names, URLs, provider
response bodies, debtor information, credentials or raw auction IDs.
These checks are *not* a substitute for verified source coverage.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Iterable

PASS = "PASS"
FAIL = "FAIL"
NOT_TESTED = "NOT_TESTED"

# These catalogue URLs identify a PROCEDURE or a trade, not a uniquely proven
# property. They cannot certify independent per-lot identity from the link alone.
PROCEDURE_LEVEL_SOURCES = frozenset({
    "fabrikant.ru", "ausib.ru", "aukcioncenter.ru", "roseltorg.ru",
    "cdtrf.ru",
})
COMPOSITE_LOT_SOURCES = frozenset({
    "nistp.ru", "vertrades.ru", "sistematorg.com",
})
KNOWN_STATUSES = frozenset({"scheduled", "active", "paused", "closed"})


def _stage(state: str, reason: str, *, count: int | None = None) -> dict[str, Any]:
    entry: dict[str, Any] = {"state": state, "reason": reason}
    if count is not None:
        entry["count"] = count
    return entry


def _raw(lot: Any) -> dict[str, Any]:
    result = getattr(lot, "raw_data", None)
    return result if isinstance(result, dict) else {}


def _price(value: Any) -> Decimal | None:
    """Return a validated RUB amount or raise ValueError (including non-finite)."""
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError("invalid_money_type")
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("invalid_money_type") from exc
    if not amount.is_finite() or not (Decimal(0) < amount < Decimal("1e15")):
        raise ValueError("invalid_money_range")
    if amount != amount.quantize(Decimal("0.01")):
        raise ValueError("invalid_money_precision")
    return amount


def audit_catalogue_lots(source: str, observed: Iterable[Any]) -> dict[str, dict[str, Any]]:
    """Inspect every observed row, not just the 1-2 selected detail probes."""
    lots = tuple(observed)
    if not lots:
        return {
            stage: _stage(NOT_TESTED, "no_observed_lots")
            for stage in ("lot_id_provenance", "money_integrity",
                          "catalogue_price_coverage", "catalogue_status_coverage")
        }

    wrong_id = 0
    procedure_unverified = 0
    invalid_money = 0
    trade_money = 0
    deposit_ambiguity = 0
    money_unverified = 0
    with_money = 0
    with_status = 0
    for lot in lots:
        ident = str(getattr(lot, "external_id", "") or "")
        raw = _raw(lot)
        if (not ident
                or getattr(lot, "source_system", None) != source
                or (source != "lot-online.ru" and not ident.startswith(source + ":"))):
            wrong_id += 1
        elif source in COMPOSITE_LOT_SOURCES:
            parts = ident[len(source) + 1:].split(":")
            if len(parts) != 2 or not all(parts) or str(raw.get("lot_number") or "") != parts[1]:
                wrong_id += 1
        if (source in PROCEDURE_LEVEL_SOURCES
                or raw.get("trade_level_listing") is True
                or raw.get("individual_lot_identity_verified") is False):
            procedure_unverified += 1

        present = False
        for name in ("start_price", "current_price"):
            value = getattr(lot, name, None)
            if value is None:
                continue
            try:
                _price(value)
            except ValueError:
                invalid_money += 1
            else:
                present = True
        if present:
            with_money += 1
            if raw.get("trade_level_listing") is True:
                trade_money += 1
        if raw.get("price_unverified") is True:
            money_unverified += 1
        try:
            start = _price(getattr(lot, "start_price", None))
            deposit = _price(getattr(lot, "deposit_amount", None))
        except ValueError:
            # Already recorded invalid listing money above if relevant; a
            # malformed deposit also requires independent verification.
            money_unverified += 1
        else:
            if start is not None and deposit is not None and start == deposit:
                deposit_ambiguity += 1
        status = str(getattr(lot, "auction_status", "") or "").lower()
        if status in KNOWN_STATUSES:
            with_status += 1

    if wrong_id:
        identity = _stage(FAIL, "invalid_source_or_composite_lot_identity", count=wrong_id)
    elif procedure_unverified:
        identity = _stage(NOT_TESTED, "procedure_level_identity_needs_per_lot_detail",
                          count=procedure_unverified)
    else:
        identity = _stage(PASS, "well_formed_catalogue_lot_identity_only", count=len(lots))

    if invalid_money:
        money = _stage(FAIL, "invalid_amount_range_or_precision", count=invalid_money)
    elif trade_money:
        money = _stage(FAIL, "trade_level_money_assigned_to_individual_lot", count=trade_money)
    elif deposit_ambiguity:
        money = _stage(NOT_TESTED, "start_price_equals_deposit_needs_detail",
                       count=deposit_ambiguity)
    elif money_unverified:
        money = _stage(NOT_TESTED, "provider_money_requires_independent_lot_proof",
                       count=money_unverified)
    else:
        money = _stage(PASS, "observed_money_is_structurally_plausible_not_detail_proven")

    return {
        "lot_id_provenance": identity,
        "money_integrity": money,
        "catalogue_price_coverage": _stage(
            PASS if with_money == len(lots) else NOT_TESTED,
            "all_observed_catalogue_lots_have_money" if with_money == len(lots)
            else "some_catalogue_prices_missing_need_detail",
            count=with_money,
        ),
        "catalogue_status_coverage": _stage(
            PASS if with_status == len(lots) else NOT_TESTED,
            "all_observed_catalogue_statuses_recognised" if with_status == len(lots)
            else "some_catalogue_statuses_unknown_need_detail",
            count=with_status,
        ),
    }


def audit_detail_pair(source: str, listing: Any, detail: Any) -> dict[str, dict[str, Any]]:
    """Verify that a fetched detail does not contradict its candidate listing.

    Do not infer positive money or status evidence from an enriched listing
    whose monetary fields were merely carried forward.
    """
    if (getattr(listing, "external_id", None) != getattr(detail, "external_id", None)
            or getattr(detail, "source_system", None) != source):
        return {
            "detail_price_consistency": _stage(FAIL, "detail_identity_mismatch"),
            "detail_status_consistency": _stage(FAIL, "detail_identity_mismatch"),
        }
    if (source in PROCEDURE_LEVEL_SOURCES or _raw(listing).get("trade_level_listing") is True
            or _raw(listing).get("individual_lot_identity_verified") is False):
        # The connector may assign requested procedure ID to a parsed detail:
        # matching the string alone does not prove per-lot asset identity.
        return {
            "detail_price_consistency": _stage(NOT_TESTED, "detail_is_procedure_not_verified_lot"),
            "detail_status_consistency": _stage(NOT_TESTED, "detail_is_procedure_not_verified_lot"),
        }
    try:
        listed_start = _price(getattr(listing, "start_price", None))
        detail_start = _price(getattr(detail, "start_price", None))
        listed_current = _price(getattr(listing, "current_price", None))
        detail_current = _price(getattr(detail, "current_price", None))
    except ValueError:
        price = _stage(FAIL, "invalid_catalogue_or_detail_money")
    else:
        if (listed_start is not None and detail_start is not None
                and abs(listed_start - detail_start) > Decimal("0.01")):
            price = _stage(FAIL, "start_price_conflicts_with_individual_detail")
        elif (listed_current is not None and detail_current is not None
                and abs(listed_current - detail_current) > Decimal("0.01")):
            # An auction's current price may legitimately change after the
            # catalogue loads. Validate recency instead of declaring fraud.
            price = _stage(NOT_TESTED, "current_price_changed_needs_recency")
        elif listed_current is not None and detail_current is None:
            price = _stage(NOT_TESTED, "listing_current_price_not_in_individual_detail")
        elif (detail_start is not None or detail_current is not None):
            price = _stage(PASS, "money_observed_in_individual_detail")
        else:
            price = _stage(NOT_TESTED, "individual_detail_money_missing")

    listed_status = str(getattr(listing, "auction_status", "") or "").lower()
    detail_status = str(getattr(detail, "auction_status", "") or "").lower()
    if detail_status not in KNOWN_STATUSES:
        status = _stage(NOT_TESTED, "individual_detail_status_unrecognised")
    elif listed_status in KNOWN_STATUSES and listed_status != detail_status:
        status = _stage(NOT_TESTED, "catalogue_detail_status_disagreement_needs_recency")
    else:
        status = _stage(PASS, "individual_detail_status_observed")
    return {
        "detail_price_consistency": price,
        "detail_status_consistency": status,
    }
