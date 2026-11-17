"""Source-scoped timeline, Moscow civil dates and asset address for Torgi Russia."""
from __future__ import annotations

from datetime import datetime
import re
from bs4 import BeautifulSoup
from bankrotai.torgi_russia_address import extract_catalogue_address


def detail_stage_fields(data: dict) -> dict:
    value = data.get("stages")
    stages = value if isinstance(value, dict) else {}
    return {
        "application_start_at": stages.get("begin_bid_time"),
        "application_deadline": stages.get("end_bid_time"),
        "auction_at": stages.get("begin_offer_time") or data.get("begin_offer_time"),
        "auction_end_at": stages.get("end_offer_time"),
    }


def collect_timeline_labels(soup: BeautifulSoup) -> dict[str, str]:
    labels: dict[str, str] = {}
    timeline_date = re.compile(
        r"^\d{2}\.\d{2}\.\d{4}\s*,?\s*\d{1,2}:\d{2}$"
    )
    lifecycle_markers = (
        "приема заявок",
        "приёма заявок",
        "предложений о цене",
        "дата торгов",
        "дата аукциона",
        "проведения торгов",
    )
    for container in soup.find_all(["div", "li"]):
        paragraphs = container.find_all("p", recursive=False)
        if len(paragraphs) < 2:
            continue
        value = paragraphs[0].get_text(" ", strip=True)
        label = paragraphs[1].get_text(" ", strip=True).rstrip(":").casefold()
        if (
            timeline_date.fullmatch(value)
            and any(marker in label for marker in lifecycle_markers)
        ):
            labels.setdefault(label, value)

    return labels


def parse_detail_datetime(value: str | None) -> datetime | None:
    if not value:
        return None

    raw = str(value).strip()

    # Next.js payload timestamps are already expressed in source-local
    # Moscow civil time and contain no UTC offset.
    for pattern in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
    ):
        try:
            return datetime.strptime(raw, pattern)
        except ValueError:
            pass

    match = re.search(
        r"\d{2}\.\d{2}\.\d{4}"
        r"(?:\s*,?\s*[вВ]?\s*\d{1,2}:\d{2})?",
        raw,
    )
    if not match:
        return None

    normalized = re.sub(r",\s*", " ", match.group(0))
    normalized = re.sub(r"\s+[вВ]\s*", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()

    for pattern in ("%d.%m.%Y %H:%M", "%d.%m.%Y"):
        try:
            return datetime.strptime(normalized, pattern)
        except ValueError:
            pass
    return None



def extract_detail_property_address(description: str) -> str | None:
    normalized = re.sub(r"\s+", " ", description or "").strip()
    match = re.search(
        r"(?:по адресу|адрес(?: местонахождения)?)\s*:\s*(.+?)"
        r"(?=\s*[,;]?\s*(?:К[\\/]?н|Кадастров)|$)",
        normalized,
        flags=re.IGNORECASE,
    )
    return extract_catalogue_address(normalized) or (match.group(1).strip(" ,.;") if match else None)

