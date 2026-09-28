from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from hashlib import sha256
from urllib.parse import parse_qs, urldefrag, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from bankrotai.domain import NormalizedLot
from bankrotai.regions import normalize_region_code
from bankrotai.scraper_contracts import TorgiRussiaSearchFilters, parse_money


BASE_URL = "https://xn----etbpba5admdlad.xn--p1ai"
PUBLIC_SEARCH_PATH = "/search"
PUBLIC_PAGE_SIZE = 24
REAL_ESTATE_CATEGORY_IDS = (7, 33, 343, 9, 18, 17, 16)
# The public catalog's regions[] parameter is an internal sequential filter ID,
# not the canonical subject code after 79. Verified live on 2026-09-28:
# 80=Nenets AO, 81=KhMAO, 82=Chukotka, 83=Yamal, 84=Crimea,
# 85=Sevastopol, 86="other territories". IDs 87+ currently return no rows.
PUBLIC_REGION_FILTER_EXPECTATIONS: dict[int, str | None] = {
    **{region_id: str(region_id).zfill(2) for region_id in range(1, 80)},
    80: "83",
    81: "86",
    82: "87",
    83: "89",
    84: "82",
    85: "92",
    86: None,
}
PUBLIC_REGION_FILTER_IDS = tuple(PUBLIC_REGION_FILTER_EXPECTATIONS)
PUBLIC_OTHER_REGION_TITLE = "Иные территории, включая город и космодром Байконур"
CADASTRAL_RE = re.compile(r"\b\d{2}\s*:\s*\d{2}\s*:\s*\d{5,7}\s*:\s*\d+\b")
LOT_OBJECT_START_RE = re.compile(r'\{"id":\d+,"title":')


def normalize_cadastral_number(value: str) -> str:
    return re.sub(r"\s+", "", value or "")


@dataclass(slots=True)
class TorgiRussiaDetails:
    torgi_russia_url: str | None = None
    gis_torgi_url: str | None = None
    etp_url: str | None = None
    image_urls: list[str] = field(default_factory=list)
    cadastral_numbers: list[str] = field(default_factory=list)
    description: str | None = None
    procedure_number: str | None = None
    address: str | None = None
    category: str | None = None
    application_start_at: datetime | None = None
    application_deadline: datetime | None = None
    auction_at: datetime | None = None

    def as_dict(self) -> dict:
        return {
            "torgi_russia_url": self.torgi_russia_url,
            "gis_torgi_url": self.gis_torgi_url,
            "etp_url": self.etp_url,
            "torgi_russia_image_urls": list(self.image_urls),
            "cadastral_numbers": list(self.cadastral_numbers),
            "description": self.description,
            "address": self.address,
            "category": self.category,
            "application_start_at": self.application_start_at,
            "application_deadline": self.application_deadline,
            "auction_at": self.auction_at,
        }


class TorgiRussiaClient:
    """Read the public Next.js site without depending on the retired API host."""

    def __init__(self, *, timeout: float = 30, session: requests.Session | None = None):
        self.timeout = timeout
        self.session = session or requests.Session()
        self.session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
                ),
                "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.7",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Cache-Control": "no-cache",
            }
        )

    def find_by_cadastral_numbers(self, values: list[str]) -> TorgiRussiaDetails | None:
        cadastral_numbers = [normalize_cadastral_number(value) for value in values if value]
        for cadastral_number in dict.fromkeys(cadastral_numbers):
            response = self.session.get(
                urljoin(BASE_URL, PUBLIC_SEARCH_PATH),
                params={"search": cadastral_number, "page": 1},
                timeout=self.timeout,
            )
            response.raise_for_status()
            records, _ = self.parse_next_search_payload(
                response.text,
                page_url=response.url,
                current_page=1,
            )
            lot_url = self._matching_lot_url_from_payload({"data": records}, cadastral_number)
            if lot_url:
                detail = self.session.get(lot_url, timeout=self.timeout)
                detail.raise_for_status()
                return self.parse_lot_page(detail.text, detail.url or lot_url)
        return None

    def search_lots(self, filters: TorgiRussiaSearchFilters) -> tuple[list[NormalizedLot], dict]:
        page = max(1, int(filters.page))
        category_ids = (
            REAL_ESTATE_CATEGORY_IDS
            if str(filters.category_id) == "6"
            else (int(filters.category_id),)
        )
        params: list[tuple[str, str | int]] = [("search", "")]
        params.extend(("categorie_childs[]", category_id) for category_id in category_ids)
        params.append(("page", page))
        if filters.history_only:
            params.append(("history_only", 1))
        if filters.region_id is not None:
            params.append(("regions[]", int(filters.region_id)))

        response = self.session.get(
            urljoin(BASE_URL, PUBLIC_SEARCH_PATH),
            params=params,
            timeout=self.timeout,
        )
        response.raise_for_status()
        records, page_meta = self.parse_next_search_payload(
            response.text,
            page_url=response.url,
            current_page=page,
        )

        if filters.region_id is not None and records:
            source_region_id = int(filters.region_id)
            expected_code = PUBLIC_REGION_FILTER_EXPECTATIONS.get(source_region_id)
            raw_titles = {
                str(item.get("region_title") or "").strip()
                for item in records
                if item.get("region_title")
            }
            if source_region_id not in PUBLIC_REGION_FILTER_EXPECTATIONS:
                raise RuntimeError(
                    "Torgi Russia public region filter is not supported: "
                    f"source_region_id={source_region_id}"
                )
            if expected_code is None:
                if raw_titles != {PUBLIC_OTHER_REGION_TITLE}:
                    raise RuntimeError(
                        "Torgi Russia public region filter was not applied: "
                        f"source_region_id={source_region_id} expected={PUBLIC_OTHER_REGION_TITLE!r} "
                        f"observed={sorted(raw_titles)}"
                    )
            else:
                observed_codes = {
                    code
                    for title in raw_titles
                    for code in [normalize_region_code(title)]
                    if code is not None
                }
                if observed_codes != {expected_code}:
                    raise RuntimeError(
                        "Torgi Russia public region filter was not applied: "
                        f"source_region_id={source_region_id} expected={expected_code} "
                        f"observed={sorted(observed_codes)} titles={sorted(raw_titles)}"
                    )

        payload = {"data": records}
        lots = self.parse_search_payload(payload, history_only=filters.history_only)
        for lot in lots:
            raw = dict(lot.raw_data or {})
            raw["raw_endpoint"] = response.url
            raw["transport"] = "public-nextjs-html"
            lot.raw_data = raw

        return lots, {
            "source": "torgi-russia.ru",
            "page": page,
            "loaded": len(lots),
            "has_more": bool(page_meta["has_more"]),
            "total_pages": page_meta["total_pages"],
            "total": page_meta.get("total"),
            "per_page": page_meta.get("per_page"),
            "raw_endpoint": response.url,
            "region_id": filters.region_id,
            "transport": "public-nextjs-html",
        }

    def fetch_lot_page(self, external_id: str) -> tuple[str, str]:
        numeric_id = external_id.rsplit(":", 1)[-1]
        lot_url = urljoin(BASE_URL, f"/lot/{numeric_id}")
        response = self.session.get(lot_url, timeout=self.timeout)
        response.raise_for_status()
        return response.text, response.url or lot_url

    @staticmethod
    def _extract_next_flight_text(html: str) -> str:
        soup = BeautifulSoup(html, "html.parser")
        parts: list[str] = []
        for script in soup.find_all("script"):
            script_text = script.string or script.get_text() or ""
            match = re.fullmatch(r"\s*self\.__next_f\.push\((.*)\)\s*", script_text, flags=re.DOTALL)
            if match is None:
                continue
            try:
                payload = json.loads(match.group(1))
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if isinstance(payload, list) and len(payload) >= 2 and isinstance(payload[1], str):
                parts.append(payload[1])
        return "".join(parts)

    @staticmethod
    def _extract_named_json_value(text: str, key: str):
        marker = f'"{key}":'
        start = text.find(marker)
        if start < 0:
            return None
        start += len(marker)
        try:
            value, _end = json.JSONDecoder().raw_decode(text, start)
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
        return value

    @staticmethod
    def _extract_balanced_json_object(text: str, start: int) -> str | None:
        depth = 0
        in_string = False
        escaped = False
        for index in range(start, len(text)):
            char = text[index]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    return text[start : index + 1]
        return None

    @classmethod
    def _extract_public_lot_records(cls, html: str, *, require_linked: bool = True) -> list[dict]:
        flight = cls._extract_next_flight_text(html)
        initial_lots = cls._extract_named_json_value(flight, "initialLots")
        records: dict[int, dict] = {}
        if isinstance(initial_lots, list):
            for item in initial_lots:
                if isinstance(item, dict) and isinstance(item.get("id"), int):
                    records[item["id"]] = item

        # Compatibility fallback for early/new variants that embed the same
        # objects under a generic component property rather than initialLots.
        for match in LOT_OBJECT_START_RE.finditer(flight):
            raw_object = cls._extract_balanced_json_object(flight, match.start())
            if raw_object is None:
                continue
            try:
                item = json.loads(raw_object)
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if (
                isinstance(item, dict)
                and isinstance(item.get("id"), int)
                and isinstance(item.get("title"), str)
                and (
                    isinstance(item.get("region"), dict)
                    or isinstance(item.get("region_title"), str)
                )
                and ("start_price" in item or "current_price" in item)
            ):
                records[item["id"]] = item

        # The SSR payload can contain unrelated widgets. Keep only records that
        # are also represented by a public /lot/{id} link when the DOM exposes
        # such links. This preserves the search result set while avoiding JSON
        # objects from hidden/global data.
        soup = BeautifulSoup(html, "html.parser")
        linked_ids: set[int] = set()
        for anchor in soup.select("a[href*='/lot/']"):
            candidate = urljoin(BASE_URL, str(anchor.get("href") or ""))
            link_match = re.fullmatch(r"/lot/(\d+)/?", urlparse(candidate).path)
            if link_match:
                linked_ids.add(int(link_match.group(1)))
        if require_linked and linked_ids:
            records = {key: value for key, value in records.items() if key in linked_ids}

        return list(records.values())

    @staticmethod
    def _pagination_metadata(html: str, *, page_url: str, current_page: int, loaded: int) -> dict:
        flight = TorgiRussiaClient._extract_next_flight_text(html)
        initial_meta = TorgiRussiaClient._extract_named_json_value(flight, "initialMeta")
        if isinstance(initial_meta, dict):
            last_page = max(current_page, int(initial_meta.get("last_page") or current_page))
            return {
                "has_more": current_page < last_page,
                "total_pages": last_page,
                "total": initial_meta.get("total"),
                "per_page": initial_meta.get("per_page"),
            }

        last_page_match = re.search(r'"(?:last_page|lastPage)"\s*:\s*(\d+)', flight)
        total_match = re.search(r'"total"\s*:\s*(\d+)', flight)
        if last_page_match:
            last_page = max(current_page, int(last_page_match.group(1)))
            return {
                "has_more": current_page < last_page,
                "total_pages": last_page,
                "total": int(total_match.group(1)) if total_match else None,
                "per_page": None,
            }

        soup = BeautifulSoup(html, "html.parser")
        observed_pages: set[int] = {max(1, current_page)}
        for anchor in soup.select("a[href]"):
            candidate = urljoin(page_url, str(anchor.get("href") or ""))
            try:
                values = parse_qs(urlparse(candidate).query).get("page") or []
                observed_pages.update(int(value) for value in values if str(value).isdigit())
            except (TypeError, ValueError):
                continue

        larger_pages = [page for page in observed_pages if page > current_page]
        if larger_pages:
            return {
                "has_more": True,
                "total_pages": max(observed_pages),
                "total": None,
                "per_page": None,
            }

        # The verified new SSR search renders 24 lots per page. If pagination
        # controls are client-only, one extra request after an exact full final
        # page is harmless and safer than silently truncating a complete source.
        has_more = loaded >= PUBLIC_PAGE_SIZE
        return {
            "has_more": has_more,
            "total_pages": current_page + 1 if has_more else current_page,
            "total": None,
            "per_page": PUBLIC_PAGE_SIZE,
        }

    @classmethod
    def parse_next_search_payload(
        cls,
        html: str,
        *,
        page_url: str,
        current_page: int,
    ) -> tuple[list[dict], dict]:
        records = cls._extract_public_lot_records(html)
        metadata = cls._pagination_metadata(
            html,
            page_url=page_url,
            current_page=current_page,
            loaded=len(records),
        )
        return records, metadata

    @staticmethod
    def parse_detail_payload(data: dict) -> dict:
        information_html = str(data.get("information") or "")
        description = BeautifulSoup(information_html, "html.parser").get_text("\n", strip=True)
        raw_cadastrals = data.get("cadastrals")
        cadastral_values: list = raw_cadastrals if isinstance(raw_cadastrals, list) else []
        cadastres = [normalize_cadastral_number(str(value)) for value in cadastral_values if value]
        if not cadastres:
            cadastres = [normalize_cadastral_number(value) for value in CADASTRAL_RE.findall(description)]
        address_match = re.search(
            r"(?:по адресу|адрес(?: местонахождения)?):\s*(.+?)"
            r"(?=\s+(?:К[\\/]?н|Кадастров)|\n|$)",
            description,
            flags=re.IGNORECASE,
        )
        raw_pictures = data.get("pictures")
        pictures: list = raw_pictures if isinstance(raw_pictures, list) else []
        image_urls = [
            str(item.get("link") or item.get("thumb_link"))
            for item in pictures
            if isinstance(item, dict) and (item.get("link") or item.get("thumb_link"))
        ]
        return {
            "description": description,
            "address": address_match.group(1).strip(" .;") if address_match else None,
            "cadastral_numbers": list(dict.fromkeys(cadastres)),
            "image_urls": list(dict.fromkeys(image_urls)),
            "etp_url": data.get("trade_link"),
            "source_status": data.get("status"),
            "updated_at": data.get("updated_at"),
        }

    @staticmethod
    def parse_search_payload(payload: object, *, history_only: bool = False) -> list[NormalizedLot]:
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
            raise RuntimeError("Torgi Russia search returned an invalid payload")
        lots: list[NormalizedLot] = []
        for item in payload["data"]:
            if not isinstance(item, dict) or not isinstance(item.get("id"), int):
                continue
            external_id = str(item["id"])
            title = str(item.get("title") or "").strip()
            if not title:
                continue
            region_value = item.get("region")
            region: dict = region_value if isinstance(region_value, dict) else {}
            region_name = (
                str(item.get("region_title") or region.get("title") or "").strip()
                or None
            )
            status_value = item.get("status")
            status = (
                status_value
                if isinstance(status_value, (dict, str))
                else {}
            )
            pictures_value = item.get("pictures")
            pictures: list = pictures_value if isinstance(pictures_value, list) else []
            photos = [
                str(picture.get("url") or picture.get("link") or picture.get("thumb_link"))
                for picture in pictures
                if isinstance(picture, dict)
                and (picture.get("url") or picture.get("link") or picture.get("thumb_link"))
            ]
            cadastres = [normalize_cadastral_number(value) for value in CADASTRAL_RE.findall(title)]
            lot_url = urljoin(BASE_URL, f"/lot/{external_id}")
            start_price = parse_money(str(item.get("start_price") or ""))
            current_price = parse_money(str(item.get("current_price") or ""))
            lots.append(
                NormalizedLot(
                    external_id=f"torgi-russia:{external_id}",
                    source="torgi-russia",
                    source_system="torgi-russia.ru",
                    title=title[:500],
                    description=title[:5000],
                    category="real_estate",
                    region_slug=normalize_region_code(region_name),
                    region_name=region_name,
                    address=None,
                    cadastral_number=cadastres[0] if cadastres else None,
                    vin=None,
                    area=None,
                    start_price=start_price,
                    current_price=current_price,
                    auction_status="archived" if history_only else "active",
                    lot_url=lot_url,
                    source_url=lot_url,
                    detail_level="search",
                    raw_data={
                        "raw_endpoint": urljoin(BASE_URL, PUBLIC_SEARCH_PATH),
                        "image_urls": list(dict.fromkeys(photos)),
                        "cadastral_numbers": list(dict.fromkeys(cadastres)),
                        "source_status": status,
                        "trade_link": item.get("trade_link"),
                        "category_ids": item.get("category_ids"),
                        "marketplace": item.get("marketplace") or item.get("marketplace_title"),
                        "trade_type": item.get("trade_type") or item.get("trade_type_title"),
                        "trade_form": item.get("trade_form"),
                        "begin_offer_time": item.get("begin_offer_time"),
                        "status_id": item.get("status_id"),
                        "days_remaining": item.get("days_remaining"),
                        "listing_fingerprint": sha256(
                            json.dumps(
                                {
                                    "title": title,
                                    "start_price": start_price,
                                    "current_price": current_price,
                                    "photos": photos,
                                    "status": status,
                                },
                                ensure_ascii=False,
                                sort_keys=True,
                            ).encode()
                        ).hexdigest(),
                    },
                )
            )
        return lots

    @staticmethod
    def _matching_lot_url_from_payload(payload: object, cadastral_number: str) -> str | None:
        expected = normalize_cadastral_number(cadastral_number)
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
            return None
        for item in payload["data"]:
            if not isinstance(item, dict):
                continue
            observed = {
                normalize_cadastral_number(value)
                for value in CADASTRAL_RE.findall(str(item.get("title") or ""))
            }
            if expected in observed and isinstance(item.get("id"), int):
                return urljoin(BASE_URL, f"/lot/{item['id']}")
        return None

    @staticmethod
    def parse_search_page(html: str, page_url: str) -> list[NormalizedLot]:
        """Compatibility parser for the pre-Next.js card markup."""
        soup = BeautifulSoup(html, "html.parser")
        lots: list[NormalizedLot] = []
        for card in soup.select("main article.card"):
            title_link = card.select_one("h3.card__title a[href*='/lot/']")
            if title_link is None:
                continue
            lot_url = urljoin(page_url, str(title_link.get("href") or ""))
            match = re.search(r"/lot/(\d+)", urlparse(lot_url).path)
            if match is None:
                continue
            external_id = match.group(1)
            title = title_link.get_text(" ", strip=True)
            excerpt = card.select_one(".card__excerpt")
            description = excerpt.get_text(" ", strip=True) if excerpt else title
            bid = card.select_one(".card__bids")
            start_price = parse_money(str(bid.get("data-start-bid") or "")) if bid else None
            current_price = parse_money(str(bid.get("data-current-bid") or "")) if bid else None
            meta = [item.get_text(" ", strip=True) for item in card.select(".card-meta__item")]
            region_name = next((item for item in meta if normalize_region_code(item)), None)
            region_code = normalize_region_code(region_name)
            cadastres = [
                normalize_cadastral_number(item)
                for item in CADASTRAL_RE.findall(f"{title} {description}")
            ]
            gallery = card.select_one(".card-gallery")
            photos: list[str] = []
            if gallery:
                try:
                    raw_photos = json.loads(str(gallery.get("data-photos") or "[]"))
                except (TypeError, ValueError, json.JSONDecodeError):
                    raw_photos = []
                photos = [
                    urljoin(page_url, str(item.get("url")))
                    for item in raw_photos
                    if isinstance(item, dict) and item.get("url")
                ]
            lots.append(
                NormalizedLot(
                    external_id=f"torgi-russia:{external_id}",
                    source="torgi-russia",
                    source_system="torgi-russia.ru",
                    title=title[:500],
                    description=description[:5000],
                    category="real_estate",
                    region_slug=region_code,
                    region_name=region_name,
                    address=None,
                    cadastral_number=cadastres[0] if cadastres else None,
                    vin=None,
                    area=None,
                    start_price=start_price,
                    current_price=current_price,
                    auction_status="archived" if "history_only=1" in page_url else "active",
                    lot_url=lot_url,
                    source_url=lot_url,
                    detail_level="search",
                    raw_data={
                        "raw_endpoint": page_url,
                        "image_urls": list(dict.fromkeys(photos)),
                        "cadastral_numbers": list(dict.fromkeys(cadastres)),
                        "listing_fingerprint": sha256(
                            json.dumps(
                                {
                                    "title": title,
                                    "description": description,
                                    "start_price": start_price,
                                    "current_price": current_price,
                                    "photos": photos,
                                },
                                ensure_ascii=False,
                                sort_keys=True,
                            ).encode()
                        ).hexdigest(),
                    },
                )
            )
        return lots

    @staticmethod
    def _matching_lot_url(html: str, cadastral_number: str) -> str | None:
        soup = BeautifulSoup(html, "html.parser")
        expected = normalize_cadastral_number(cadastral_number)
        for article in soup.select("article"):
            observed = {
                normalize_cadastral_number(item)
                for item in CADASTRAL_RE.findall(article.get_text(" "))
            }
            if expected not in observed:
                continue
            for anchor in article.select('a[href*="/lot/"]'):
                candidate = urljoin(BASE_URL, str(anchor.get("href") or ""))
                parsed = urlparse(candidate)
                if re.fullmatch(r"/lot/\d+/?", parsed.path):
                    return urldefrag(candidate).url
        return None

    @classmethod
    def parse_lot_page(cls, html: str, page_url: str) -> TorgiRussiaDetails:
        soup = BeautifulSoup(html, "html.parser")
        labels: dict[str, str] = {}
        for term in soup.select("dt"):
            value = term.find_next_sibling("dd")
            if value:
                labels[term.get_text(" ", strip=True).lower()] = value.get_text(" ", strip=True)
        for row in soup.select("tr"):
            cells = row.select("th, td")
            if len(cells) >= 2:
                labels[cells[0].get_text(" ", strip=True).lower()] = cells[1].get_text(" ", strip=True)
        for row in soup.select(".lot-data__text"):
            label_node = row.select_one("span")
            if label_node is None:
                continue
            label = label_node.get_text(" ", strip=True).rstrip(":").casefold()
            value = row.get_text(" ", strip=True)
            prefix = label_node.get_text(" ", strip=True)
            if value.startswith(prefix):
                value = value[len(prefix) :].strip()
            if label and value:
                labels[label] = value

        def labelled(*needles: str) -> str | None:
            return next(
                (value for key, value in labels.items() if any(needle in key for needle in needles)),
                None,
            )

        def parse_date(value: str | None) -> datetime | None:
            if not value:
                return None
            match = re.search(r"\d{2}\.\d{2}\.\d{4}(?:\s+[вВ]?\s*\d{1,2}:\d{2})?", value)
            if not match:
                return None
            normalized = re.sub(r"\s+[вВ]\s+", " ", match.group(0))
            for pattern in ("%d.%m.%Y %H:%M", "%d.%m.%Y"):
                try:
                    return datetime.strptime(normalized, pattern)
                except ValueError:
                    pass
            return None

        image_urls: list[str] = []
        gallery = soup.select_one("#lot-gallery[data-gallery]")
        if gallery:
            try:
                items = json.loads(str(gallery.get("data-gallery") or "[]"))
            except (TypeError, ValueError, json.JSONDecodeError):
                items = []
            for item in items if isinstance(items, list) else []:
                if not isinstance(item, dict):
                    continue
                candidate = item.get("url") or item.get("src")
                if candidate:
                    image_urls.append(urljoin(page_url, str(candidate)))

        gis_torgi_url = None
        etp_url = None
        for anchor in soup.select("a[href]"):
            candidate = urljoin(page_url, str(anchor.get("href") or ""))
            parsed = urlparse(candidate)
            if "torgi.gov.ru" in parsed.netloc and "/lots/lot/" in parsed.path:
                gis_torgi_url = candidate
            if "lot-online.ru" in parsed.netloc:
                etp_url = candidate

        numeric_match = re.search(r"/lot/(\d+)", urlparse(page_url).path)
        flight_record = None
        if numeric_match:
            numeric_id = int(numeric_match.group(1))
            flight_record = next(
                (
                    item
                    for item in cls._extract_public_lot_records(html, require_linked=False)
                    if item.get("id") == numeric_id
                ),
                None,
            )
        flight_detail = cls.parse_detail_payload(flight_record) if isinstance(flight_record, dict) else {}

        text = soup.get_text(" ", strip=True)
        cadastres = list(
            dict.fromkeys(
                [
                    *[normalize_cadastral_number(value) for value in CADASTRAL_RE.findall(text)],
                    *list(flight_detail.get("cadastral_numbers") or []),
                ]
            )
        )
        image_urls.extend(list(flight_detail.get("image_urls") or []))
        etp_url = flight_detail.get("etp_url") or etp_url

        return TorgiRussiaDetails(
            torgi_russia_url=urldefrag(page_url).url,
            gis_torgi_url=gis_torgi_url,
            etp_url=etp_url,
            image_urls=list(dict.fromkeys(image_urls)),
            cadastral_numbers=cadastres,
            description=str(flight_detail.get("description") or "").strip() or None,
            procedure_number=(
                match.group(0)
                if (match := re.search(r"\b[A-Z0-9]{7,12}-\d{4}-\d{4}-\d\b", text))
                else None
            ),
            address=flight_detail.get("address") or labelled("адрес", "местонахожд"),
            category=labelled("категор", "вид имущества"),
            application_start_at=parse_date(labelled("начало приема заявок", "начало приёма заявок")),
            application_deadline=parse_date(
                labelled(
                    "конец приема заявок",
                    "конец приёма заявок",
                    "окончание приема заявок",
                    "окончание приёма заявок",
                )
            ),
            auction_at=parse_date(
                labelled(
                    "конец приема ценовых предложений",
                    "конец приёма ценовых предложений",
                    "дата торгов",
                    "дата аукцион",
                    "проведен",
                )
            ),
        )
