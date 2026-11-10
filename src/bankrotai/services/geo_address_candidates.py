"""Conservative, source-independent Russian auction address candidates.

Pure functions; no requests or database writes. Re-exported by bankrotai.geo
for compatibility with existing callers and tests.
"""
from __future__ import annotations

import re


def build_geocoding_address_candidates(
    address: str | None,
    *,
    title: str | None = None,
    description: str | None = None,
    region_name: str | None = None,
    cadastral_number: str | None = None,
) -> list[str]:
    """Build conservative Nominatim queries from Russian auction-card addresses."""
    from bankrotai.services.property_address_evidence import (
        prefer_evidenced_property_address,
    )

    # Historical rows may still carry a seller/organizer address while the
    # asset's own title or description specifies its true street and building.
    # The evidence helper operates on asset-scoped fields only, requires an
    # explicit address label and rejects contradictory cadastral regions.
    value = (prefer_evidenced_property_address(
        address, title=title, description=description,
        cadastral_number=cadastral_number,
    ) or "").strip()
    address_is_incomplete = is_incomplete_address(value)
    if (not value or address_is_incomplete) and (title or description):
        # Keep this fallback aligned with extractors.extract_address so that
        # already imported shallow LOT-ONLINE cards can be geocoded without a
        # full re-import.
        from bankrotai.extractors import extract_address

        source_text = " ".join(part for part in (title, description) if part)
        extracted = extract_best_numbered_address(source_text) or extract_address(source_text) or ""
        if extracted:
            value = extracted
    if not value:
        return []

    value = re.sub(r"\.{3,}$", "", value)
    value = re.sub(
        r",?\s*(?:с|вместе\s+с)\s+земельн(?:ым|ого)\s+участк(?:ом|а).*$",
        "",
        value,
        flags=re.IGNORECASE,
    )
    # Source cards frequently append legal prose to the address. Large queries
    # make every Photon miss consume the full timeout and reduce match quality.
    value = re.split(
        r"(?:[,.;]\s*|—\s*)(?:зарегистрированные\s+обременения|"
        r"разрешенный\s+вид\s+использования|вид\s+разрешенного\s+использования|"
        r"категория\s+земель|общей\s+площадью|территориальная\s+зона|"
        r"отправив\s+предварительно\s+запрос|направив\s+предварительно\s+запрос|"
        r"осмотр\s+имущества|порядок\s+ознакомления)\b",
        value,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0]
    # Photon receives the query in the URL. Even malformed source records must
    # never be able to produce a 414 response or multi-kilobyte log entry.
    value = value[:512]
    value = re.sub(r",\s*с\.\s*п\.\s*[^,]+", "", value, flags=re.IGNORECASE)
    value = re.sub(
        r"(?:м\.\s*)?р-н\s+([^,]+)",
        lambda match: f"{match.group(1).strip()} муниципальный округ",
        value,
        flags=re.IGNORECASE,
    )
    value = re.sub(
        r"([^,]+?)\s+(?:р-н|район)(?=,|$)",
        lambda match: f"{match.group(1).strip()} муниципальный округ",
        value,
        flags=re.IGNORECASE,
    )
    value = re.sub(
        r"\bПереславский муниципальный округ\b",
        "Переславль-Залесский муниципальный округ",
        value,
        flags=re.IGNORECASE,
    )
    value = re.sub(
        r"\b\u0434\.\s*(?=[\u0410-\u042f\u0401A-Z])",
        "\u0434\u0435\u0440\u0435\u0432\u043d\u044f ", value, flags=re.IGNORECASE,
    )
    replacements = (
        # Russian source cards often omit dots from municipal abbreviations.
        # Interpret "д Прологи" as a village and "д 46" as a house number,
        # not as the same ambiguous token.
        (r"\bд\.?(?=\s+[А-ЯЁ])", "деревня"),
        (r"\bд\.?(?=\s+\d)", "дом"),
        (r"\bс\.?(?=\s+[А-ЯЁ])", "село"),
        (r"\bг\.?(?=\s+[А-ЯЁ])", "город"),
        (r"\bул\.?(?=\s+[А-ЯЁ])", "улица"),
        (r"\bпр-кт\b", "проспект"),
        (r"\bпр-т\b", "проспект"),
        (r"\bобл\.(?=\s|,|$)", "область"),
        (r"\bг\.(?=\s)", "город "),
        (r"\bп\.(?=\s)", "поселок "),
        (r"\bс\.(?=\s)", "село "),
        (r"\bд\.(?=\s)", "дом "),
        (r"\bул\.(?=\s)", "улица "),
        (r"\bкорп\.(?=\s*\w)", "корпус "),
        (r"\bстр\.(?=\s*\w)", "строение "),
        (r"\bвл\.(?=\s*\w)", "владение "),
        (r"\bлит\.(?=\s*\w)", "литера "),
        (r"\bпр-т\b", "проспект"),
        (r"\bпер\.(?=\s)", "переулок "),
    )
    for pattern, replacement in replacements:
        value = re.sub(pattern, replacement, value, flags=re.IGNORECASE)
    value = re.sub(r"\s+", " ", value).strip(" ,.;")
    if region_name and region_name.casefold() not in value.casefold():
        value = f"{value}, {region_name}"

    parts = [part.strip(" ,.;") for part in value.split(",") if part.strip(" ,.;")]
    region = next((part for part in parts if "област" in part.casefold()), region_name or "")
    district = next(
        (part for part in parts if "муниципальн" in part.casefold() or "район" in part.casefold()),
        "",
    )
    localities = [
        part
        for part in parts
        if re.search(
            r"\b(?:город|село|поселок|деревня|рабочий поселок)\b",
            part,
            re.IGNORECASE,
        )
    ]
    locality = localities[-1] if localities else ""
    locality_query = re.sub(
        r"^(?:город|село|поселок|деревня|рабочий поселок)\s+",
        "",
        locality,
        flags=re.IGNORECASE,
    )
    street = next(
        (
            part
            for part in parts
            if re.search(
                r"\b(?:улица|проспект|шоссе|переулок|проезд|набережная|площадь|тракт)\b",
                part,
                re.I,
            )
        ),
        "",
    )
    house = next(
        (
            part
            for part in parts
            if re.search(r"\b(?:дом|владение)\s*\d", part, re.IGNORECASE)
        ),
        "",
    )
    house_number = re.sub(r"^(?:дом|владение)\s*", "", house, flags=re.IGNORECASE) if house else ""

    candidates = [value]
    unit_stripped = re.sub(
        r",?\s*(?:помещение|пом\.?|квартира|кв\.?|комната|офис|"
        r"машино-место|м\s*/\s*м)\s*[№#]?\s*[\w/-]+.*$",
        "",
        value,
        flags=re.IGNORECASE,
    ).strip(" ,.;")
    if unit_stripped and unit_stripped.casefold() != value.casefold():
        candidates.append(unit_stripped)
    if street and locality_query:
        candidates.append(", ".join(part for part in (house_number, street, locality_query, district, region) if part))
    if locality_query and district:
        candidates.append(", ".join(part for part in (locality_query, district, region) if part))
    if locality_query and region:
        candidates.append(f"{locality_query}, {region}")

    # Auction cards frequently keep a vague address in the structured field
    # while the exact street/house appears only in title or description.
    # Preserve the structured address as the first choice, but surface one
    # exact numbered supplemental candidate for the local Photon fallback.
    source_text = " ".join(part for part in (title, description) if part)
    supplemental = extract_best_numbered_address(source_text) if source_text else None
    if supplemental:
        supplemental = supplemental.strip(" ,.;")
        if region_name and region_name.casefold() not in supplemental.casefold():
            supplemental = f"{supplemental}, {region_name}"
        candidates.append(supplemental)

    unique: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        normalized = " ".join(candidate.split()).strip(" ,")
        key = normalized.casefold()
        if normalized and key not in seen:
            seen.add(key)
            unique.append(normalized)
    return unique


def extract_best_numbered_address(text: str | None) -> str | None:
    """Prefer an address that contains a real house number over an earlier placeholder."""
    if not text:
        return None
    compact = re.sub(r"\s+", " ", text)
    pattern = re.compile(
        r"(?:г\.|город)\s*[^,.;]+,\s*"
        r"(?:ул\.|улица|проспект|пр-т|пер\.)\s*[^,.;]+,\s*"
        r"(?:д\.|дом)\s*[0-9][^,.;]*",
        re.IGNORECASE,
    )
    matches = [match.group(0).strip(" ,.;") for match in pattern.finditer(compact)]
    return max(matches, key=len) if matches else None


def is_incomplete_address(value: str | None) -> bool:
    return bool(
        value
        and re.search(
            r"\b(?:д|дом)\.?\s*(?:помещени[ея]|объект[а-я]*|имуще(?:ство|ства))\b",
            value,
            re.IGNORECASE,
        )
    )

