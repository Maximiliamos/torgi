from __future__ import annotations

import re


def extract_catalogue_address(value: str) -> str | None:
    """Keep an explicit street/building address and drop apartment/unit tails."""
    match = re.search(r"(?:^|[,.;]\\s*)адрес\\s*:\\s*(.+)", value or "", re.IGNORECASE)
    if not match:
        return None
    address = match.group(1)
    address = re.split(
        r"(?=,\\s*(?:кв(?:артира)?|пом(?:ещение)?|офис|комн(?:ата)?)\\.?\\s*\\d+\\b)",
        address,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0]
    address = re.split(
        r"(?=\\s+(?:собств|взыск|цена)\\s*:)",
        address,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0]
    cleaned = re.sub(r"\\s+", " ", address).strip(" ,.;")
    return cleaned or None
