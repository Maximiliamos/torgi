"""Lot-scoped cadastral evidence and Moscow timestamps from official JSON."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from bankrotai.extractors import extract_cadastral_numbers
import re


def apply_export_transaction_scope(lots, scope):
    """A mixed ZK auction family is not proof of a rental transaction.

The explicit contract in the row takes precedence over the request filter.
Keep the original family label separately for provenance.
"""
    for lot in lots:
        raw = dict(lot.raw_data or {})
        original_value = raw.get("raw")
        original = original_value if isinstance(original_value, dict) else {}
        for field, header in (
            ("application_start_at", "Дата и время начала подачи заявок"),
            ("application_deadline", "Дата и время окончания подачи заявок"),
            ("auction_at", "Дата и время начала аукциона"),
        ):
            value = str(original.get(header) or "")
            if re.search(r"\(МСК(?:[+-]\d+)?\)", value, re.I):
                setattr(lot, field, parse_torgi_gov_date(value))
        if any((lot.application_start_at, lot.application_deadline, lot.auction_at)):
            lot.auction_timezone = "Europe/Moscow"
        # Preserve literal text, without letting generic persistence guess a
        # timezone for a cell whose zone was not established above.
        for alias in ("bidd_end_time", "auction_start_date"):
            if alias in raw:
                raw["export_" + alias] = raw.pop(alias)
        lot.deposit_amount = raw.get("deposit")
        lot.auction_step_amount = raw.get("auction_step")
        lot.notice_number = raw.get("notice_number") or None
        attributes = str(raw.get("additional_attributes") or "")
        contract = re.search(r"вид\s+договора\s*[:–—-]?\s*договор(?:а)?\s+(аренды|купли[-–—\s]+продажи)", attributes, re.I)
        transaction = ("RENT" if contract[1].casefold() == "аренды" else "SALE") if contract else str(scope or "").upper()
        if transaction in {"SALE", "RENT", "RENTAL", "LEASE"}:
            raw["bidding_type"] = raw.get("bidding_type") or raw.get("trade_type")
            raw["trade_type"] = raw["typeTransaction"] = transaction
        lot.raw_data = raw


def collect_torgi_gov_cadastres(payload: dict[str, Any], text: str) -> list[str]:
    wanted = {"cadastralnumbers", "cadastralnumber", "cadnumber", "cadnumbers"}
    found: list[str] = []

    def append(value: Any) -> None:
        if isinstance(value, dict):
            for child in value.values():
                append(child)
        elif isinstance(value, list):
            for child in value:
                append(child)
        elif value is not None:
            found.extend(extract_cadastral_numbers(str(value)))

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if str(key).casefold() in wanted:
                    append(child)
                else:
                    walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(payload)
    found.extend(extract_cadastral_numbers(text))
    return list(dict.fromkeys(found))


def parse_torgi_gov_date(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or "").strip()
        if not text:
            return None
        labelled = re.fullmatch(r"(.+?)\s*\(МСК([+-]\d{1,2})?\)", text, re.I)
        if labelled:
            shift = int(labelled[2] or 0)
            if abs(shift) > 12:
                return None
            parsed_labelled = parse_torgi_gov_date(labelled[1].strip())
            return parsed_labelled - timedelta(hours=shift) if parsed_labelled is not None else None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            for pattern in ("%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M", "%d.%m.%Y", "%Y-%m-%d", "%Y-%m-%d %H:%M:%S"):
                try:
                    parsed = datetime.strptime(text[:19], pattern)
                    break
                except ValueError:
                    continue
            else:
                return None
    return parsed.astimezone(ZoneInfo("Europe/Moscow")).replace(tzinfo=None) if parsed.tzinfo is not None else parsed


def normalize_torgi_gov_property_address(address: str | None, *, cadastral_number: str | None) -> str | None:
    """Keep the specific property tail of a duplicated administrative address."""
    from bankrotai.regions import region_code_from_cadastral_number, region_code_from_text
    from bankrotai.services.geo_feature_precision import expected_locality_name, property_number, property_street

    if not address or not cadastral_number:
        return address
    parts = re.split(r"\bРоссийская\s+Федерация\b", address, maxsplit=1, flags=re.I)
    if not cadastral_number or len(parts) != 2 or not parts[0].strip():
        return address
    prefix, suffix = parts
    tail = "Российская Федерация" + suffix
    # A numbered property or street in the prefix may denote a second asset.
    if property_number(prefix) or property_street(prefix) or not expected_locality_name(tail):
        return address
    region = region_code_from_text(tail)
    if not region or region != region_code_from_cadastral_number(cadastral_number):
        return address
    return tail.strip()
