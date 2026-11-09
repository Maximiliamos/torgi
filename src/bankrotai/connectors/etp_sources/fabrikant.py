from __future__ import annotations

"""Fabrikant-specific, fail-closed public HTML contract helpers."""

from dataclasses import dataclass
from datetime import datetime
import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup


PROCEDURE_PATH_RE = re.compile(r"/v2/trades/procedure/view/([^/?#]+)(?:[/?#]|$)", re.I)
LOT_PATH_RE = re.compile(
    r"/v2/trades/procedure/lot/view/([^/?#]+)/([^/?#]+)(?:[/?#]|$)", re.I
)

_SPACE_RE = re.compile(r"\s+")
_CADASTRAL_RE = re.compile(r"(?<!\d)(\d{2}:\d{2}(?::\d{1,7}){2,4})(?!\d)")
_NUMBER_RE = r"(\d[\d\s\u00a0]*(?:[.,]\d{1,2})?)"
_RUBLE_RE = r"(?:(?:российск\w*\s+)?рубл\w*\s*\(RUB\)|₽|руб(?:\.|лей)?)"

_STATUS_RE = re.compile(
    r"лот\s*№\s*\d+\s*\.?.{0,1200}?\s+-\s+"
    r"(при[её]м\s+заявок|закончено(?:\s*\([^)]*\))*|"
    r"отказ\s+организатора(?:\s*\([^)]*\))*|"
    r"приостановлено|рассмотрение\s+заявок(?:\s+и\s+подведение\s+итогов)?)",
    re.I,
)


@dataclass(frozen=True, slots=True)
class FabrikantDetailFields:
    title: str | None
    source_status_text: str | None
    start_price: float | None
    current_price: float | None
    category_text: str | None
    category: str
    address: str | None
    cadastral_number: str | None
    area: float | None
    application_deadline: datetime | None
    auction_at: datetime | None


def _clean(value: str) -> str:
    return _SPACE_RE.sub(" ", value.replace("\xa0", " ")).strip()


def _soup(html: str | bytes) -> BeautifulSoup:
    if isinstance(html, bytes):
        try:
            html = html.decode("utf-8")
        except UnicodeDecodeError:
            html = html.decode("windows-1251", errors="replace")
    return BeautifulSoup(html, "html.parser")


def _safe_public_url(base_url: str, href: str) -> str | None:
    url = urljoin(base_url, href)
    parsed = urlparse(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname not in {"fabrikant.ru", "www.fabrikant.ru"}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port not in {None, 443}
    ):
        return None
    return url


def procedure_urls(html: str | bytes, *, base_url: str) -> list[str]:
    """Return distinct official procedure cards from the search catalogue."""
    soup = _soup(html)
    result: list[str] = []
    seen: set[str] = set()
    for anchor in soup.select("a[href]"):
        url = _safe_public_url(base_url, str(anchor.get("href") or ""))
        if not url or not PROCEDURE_PATH_RE.search(urlparse(url).path):
            continue
        normalized = url.split("#", 1)[0]
        if normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result


def lot_urls(html: str | bytes, *, base_url: str) -> list[str]:
    """Return distinct individual-lot routes exposed by a procedure card."""
    soup = _soup(html)
    result: list[str] = []
    seen: set[str] = set()
    for anchor in soup.select("a[href]"):
        url = _safe_public_url(base_url, str(anchor.get("href") or ""))
        if not url:
            continue
        match = LOT_PATH_RE.search(urlparse(url).path)
        if not match:
            continue
        normalized = (
            f"https://www.fabrikant.ru/v2/trades/procedure/lot/view/"
            f"{match.group(1)}/{match.group(2)}"
        )
        if normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result


def lot_identity(url: str) -> tuple[str, str] | None:
    match = LOT_PATH_RE.search(urlparse(url).path)
    if not match:
        return None
    return match.group(1), match.group(2)


def external_id_from_lot_url(url: str) -> str | None:
    identity = lot_identity(url)
    if identity is None:
        return None
    return f"fabrikant.ru:{identity[0]}:{identity[1]}"


def _money_number(raw: str) -> float | None:
    cleaned = raw.replace("\xa0", "").replace(" ", "").replace(",", ".")
    try:
        amount = float(cleaned)
    except ValueError:
        return None
    return amount if 0 < amount < 1e15 else None


def _label_segment(text: str, label: str, stops: tuple[str, ...], limit: int = 800) -> str | None:
    match = re.search(label, text, re.I)
    if not match:
        return None
    tail = text[match.end():match.end() + limit]
    stop_positions = []
    for stop in stops:
        stop_match = re.search(stop, tail, re.I)
        if stop_match:
            stop_positions.append(stop_match.start())
    if stop_positions:
        tail = tail[:min(stop_positions)]
    value = _clean(tail).strip(" :-")
    return value or None


def fabrikant_initial_money(text: str) -> float | None:
    """Read only an explicitly labelled lot initial price.

    Deposits, auction steps and public-offer stage values are never used as
    fallback initial prices.
    """
    preferred = re.search(
        r"(?:начальная\s+цена\s+(?:предмета\s+договора|договора)|"
        r"начальная\s+цена\s+лота)"
        r"(?:(?!размер\s+задатка|шаг\s+аукциона|этап\s+понижения).){0,180}?"
        + _NUMBER_RE + r"\s*" + _RUBLE_RE,
        text,
        re.I,
    )
    if preferred:
        return _money_number(preferred.group(1))

    # Some bankruptcy templates label the top-level field simply
    # "Начальная цена". Use it only when it occurs before an explicit public-
    # offer schedule and can be tied to a RUB currency marker.
    prefix = text.split("Этап понижения", 1)[0]
    matches = list(re.finditer(
        r"начальная\s+цена\b"
        r"(?:(?!размер\s+задатка|шаг\s+аукциона).){0,160}?"
        + _NUMBER_RE + r"\s*" + _RUBLE_RE,
        prefix,
        re.I,
    ))
    if len(matches) == 1:
        return _money_number(matches[0].group(1))
    return None


def fabrikant_current_money(text: str) -> float | None:
    match = re.search(
        r"текущая\s+цена\b"
        r"(?:(?!размер\s+задатка|шаг\s+аукциона).){0,120}?"
        + _NUMBER_RE + r"\s*" + _RUBLE_RE,
        text,
        re.I,
    )
    return _money_number(match.group(1)) if match else None


def fabrikant_status(text: str) -> tuple[str, str | None]:
    match = _STATUS_RE.search(text)
    if not match:
        return "unknown", None
    raw = _clean(match.group(1))
    lowered = raw.lower()
    if re.search(r"при[её]м\s+заявок", lowered):
        return "active", raw
    if "приостанов" in lowered:
        return "paused", raw
    if "закончено" in lowered or "отказ организатора" in lowered:
        return "closed", raw
    # "Рассмотрение заявок" means application intake ended, but does not by
    # itself prove that the auction/procedure is terminal.
    return "unknown", raw


def _parse_date_after(text: str, label: str) -> datetime | None:
    match = re.search(
        label + r"[^\d]{0,80}(\d{2}\.\d{2}\.\d{4}(?:\s+\d{2}:\d{2})?)",
        text,
        re.I,
    )
    if not match:
        return None
    raw = match.group(1)
    for fmt in ("%d.%m.%Y %H:%M", "%d.%m.%Y"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            pass
    return None


def _structured_category(text: str) -> str | None:
    value = _label_segment(
        text,
        r"категория\s+объекта",
        (
            r"коды?\s+окпд",
            r"классификатор\s+имущества",
            r"количество\s+по\s+океи",
            r"местонахождение\s+объекта",
            r"начальная\s+цена",
        ),
        limit=300,
    )
    return value[:200] if value else None


def normalize_category(category_text: str | None, text: str) -> str:
    value = (category_text or "") + " " + text[:1200]
    lowered = value.lower()
    if any(token in lowered for token in (
        "земельн", "участок", "земля",
    )):
        return "land"
    if any(token in lowered for token in (
        "квартир", "комнат", "жилое помещ", "нежилое помещ", "здани",
        "сооружен", "машино-мест", "гараж", "недвижим",
    )):
        return "real_estate"
    if any(token in lowered for token in (
        "легковые автомобили", "грузовые автомобили", "автомоб", "транспорт",
    )):
        return "vehicle"
    if any(token in lowered for token in ("право требования", "дебитор")):
        return "receivables"
    return "other"


def parse_detail_fields(html: str | bytes) -> FabrikantDetailFields:
    soup = _soup(html)
    text = _clean(soup.get_text(" ", strip=True))

    title = _label_segment(
        text,
        r"предмет\s+договора",
        (
            r"категория\s+объекта",
            r"характеристики\s+объекта",
            r"коды?\s+окпд",
            r"классификатор\s+имущества",
            r"местонахождение\s+объекта",
            r"начальная\s+цена",
        ),
        limit=1500,
    )
    if not title:
        heading = re.search(
            r"лот\s*№\s*\d+\s*\.\s*(.{8,800}?)\s+-\s+"
            r"(?:при[её]м\s+заявок|закончено|отказ\s+организатора|"
            r"приостановлено|рассмотрение\s+заявок)",
            text,
            re.I,
        )
        title = _clean(heading.group(1)) if heading else None

    status, status_text = fabrikant_status(text)
    category_text = _structured_category(text)
    cadastral = _CADASTRAL_RE.search(text)

    area_match = re.search(
        r"(?:общая\s+площадь(?:,?\s*м[²2])?|площадь(?:,?\s*м[²2])?)"
        r"\s*[:=-]?\s*(\d+(?:[.,]\d+)?)",
        text,
        re.I,
    )
    if not area_match:
        area_match = re.search(r"(\d+(?:[.,]\d+)?)\s*(?:кв\.?\s*м|м[²2])", text, re.I)
    area = float(area_match.group(1).replace(",", ".")) if area_match else None

    address = None
    address_match = re.search(
        r"\bадрес\s*:\s*(.{5,500}?)(?=\s+(?:начальная\s+цена|"
        r"категория\s+объекта|коды?\s+окпд|характеристики\s+объекта|"
        r"задаток|дата\s+и\s+время|$))",
        text,
        re.I,
    )
    if address_match:
        address = _clean(address_match.group(1))

    return FabrikantDetailFields(
        title=title[:500] if title else None,
        source_status_text=status_text,
        start_price=fabrikant_initial_money(text),
        current_price=fabrikant_current_money(text),
        category_text=category_text,
        category=normalize_category(category_text, text),
        address=address,
        cadastral_number=cadastral.group(1) if cadastral else None,
        area=area,
        application_deadline=_parse_date_after(
            text, r"дата\s+и\s+время\s+окончания\s+при[её]ма\s+заявок"
        ),
        auction_at=_parse_date_after(
            text, r"дата\s+и\s+время\s+начала\s+аукциона"
        ),
    )
