from __future__ import annotations

import json
import re
from datetime import datetime
from hashlib import sha256
from dataclasses import dataclass, field
from urllib.parse import urldefrag, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from bankrotai.domain import NormalizedLot
from bankrotai.regions import normalize_region_code
from bankrotai.scraper_contracts import TorgiRussiaSearchFilters, parse_money


BASE_URL = "https://xn----etbpba5admdlad.xn--p1ai"
API_BASE_URL = "https://xn--80aqu.xn----etbpba5admdlad.xn--p1ai/api"
CADASTRAL_RE = re.compile(r"\b\d{2}\s*:\s*\d{2}\s*:\s*\d{5,7}\s*:\s*\d+\b")


def normalize_cadastral_number(value: str) -> str:
    return re.sub(r"\s+", "", value or "")


@dataclass(slots=True)
class TorgiRussiaDetails:
    torgi_russia_url: str | None = None
    gis_torgi_url: str | None = None
    etp_url: str | None = None
    image_urls: list[str] = field(default_factory=list)
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
            "address": self.address,
            "category": self.category,
            "application_start_at": self.application_start_at,
            "application_deadline": self.application_deadline,
            "auction_at": self.auction_at,
        }


class TorgiRussiaClient:
    """Find a matching Torgi Rossii card by cadastral number and parse its gallery."""

    def __init__(self, *, timeout: float = 15, session: requests.Session | None = None):
        self.timeout = timeout
        self.session = session or requests.Session()
        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "ru-RU,ru;q=0.9",
            "Accept": "application/json",
        })

    def find_by_cadastral_numbers(self, values: list[str]) -> TorgiRussiaDetails | None:
        cadastral_numbers = [normalize_cadastral_number(value) for value in values if value]
        for cadastral_number in dict.fromkeys(cadastral_numbers):
            response = self.session.post(
                f"{API_BASE_URL}/search",
                json={"search": cadastral_number, "page": 1, "history_only": 0},
                timeout=self.timeout,
            )
            response.raise_for_status()
            lot_url = self._matching_lot_url_from_payload(response.json(), cadastral_number)
            if lot_url:
                detail = self.session.get(lot_url, timeout=self.timeout)
                detail.raise_for_status()
                parsed = self.parse_lot_page(detail.text, detail.url or lot_url)
                if parsed.etp_url:
                    try:
                        with self.session.get(parsed.etp_url, timeout=self.timeout, stream=True) as etp_response:
                            etp_response.raise_for_status()
                            parsed.etp_url = etp_response.url or parsed.etp_url
                    except requests.RequestException:
                        pass
                return parsed
        return None

    def search_lots(self, filters: TorgiRussiaSearchFilters) -> tuple[list[NormalizedLot], dict]:
        payload = {
            "categorie_childs": [int(filters.category_id)],
            "history_only": 1 if filters.history_only else 0,
            "page": max(1, int(filters.page)),
        }
        response = self.session.post(f"{API_BASE_URL}/search", json=payload, timeout=self.timeout)
        response.raise_for_status()
        data = response.json()
        lots = self.parse_search_payload(data, history_only=filters.history_only)
        meta = data.get("meta") if isinstance(data, dict) else {}
        meta = meta if isinstance(meta, dict) else {}
        last_page = int(meta.get("last_page") or filters.page)
        return lots, {
            "source": "torgi-russia.ru",
            "page": filters.page,
            "loaded": len(lots),
            "has_more": int(filters.page) < last_page,
            "total_pages": last_page,
            "total": meta.get("total"),
            "raw_endpoint": response.url,
        }

    def fetch_lot_payload(self, external_id: str) -> dict:
        numeric_id = external_id.rsplit(":", 1)[-1]
        response = self.session.get(f"{API_BASE_URL}/lots/{numeric_id}", timeout=self.timeout)
        response.raise_for_status()
        payload = response.json()
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict):
            raise RuntimeError("Torgi Russia detail returned an invalid JSON payload")
        return data

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
            raise RuntimeError("Torgi Russia search returned an invalid JSON payload")
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
            region_name = str(region.get("title") or "").strip() or None
            status_value = item.get("status")
            status: dict = status_value if isinstance(status_value, dict) else {}
            pictures_value = item.get("pictures")
            pictures: list = pictures_value if isinstance(pictures_value, list) else []
            photos = [str(picture.get("link") or picture.get("thumb_link")) for picture in pictures if isinstance(picture, dict) and (picture.get("link") or picture.get("thumb_link"))]
            cadastres = [normalize_cadastral_number(value) for value in CADASTRAL_RE.findall(title)]
            lot_url = urljoin(BASE_URL, f"/lot/{external_id}")
            start_price = parse_money(str(item.get("start_price") or ""))
            current_price = parse_money(str(item.get("current_price") or ""))
            lots.append(NormalizedLot(
                external_id=f"torgi-russia:{external_id}", source="torgi-russia",
                source_system="torgi-russia.ru", title=title[:500], description=title[:5000],
                category="real_estate", region_slug=normalize_region_code(region_name),
                region_name=region_name, address=None, cadastral_number=cadastres[0] if cadastres else None,
                vin=None, area=None, start_price=start_price, current_price=current_price,
                auction_status="archived" if history_only else "active", lot_url=lot_url,
                source_url=lot_url, detail_level="search",
                raw_data={"raw_endpoint": f"{API_BASE_URL}/search", "image_urls": list(dict.fromkeys(photos)),
                    "cadastral_numbers": list(dict.fromkeys(cadastres)), "source_status": status,
                    "trade_link": item.get("trade_link"), "category_ids": item.get("category_ids"),
                    "listing_fingerprint": sha256(json.dumps({"title": title, "start_price": start_price,
                        "current_price": current_price, "photos": photos, "status": status},
                        ensure_ascii=False, sort_keys=True).encode()).hexdigest()},
            ))
        return lots

    @staticmethod
    def _matching_lot_url_from_payload(payload: object, cadastral_number: str) -> str | None:
        expected = normalize_cadastral_number(cadastral_number)
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
            return None
        for item in payload["data"]:
            if not isinstance(item, dict):
                continue
            observed = {normalize_cadastral_number(value) for value in CADASTRAL_RE.findall(str(item.get("title") or ""))}
            if expected in observed and isinstance(item.get("id"), int):
                return urljoin(BASE_URL, f"/lot/{item['id']}")
        return None

    @staticmethod
    def parse_search_page(html: str, page_url: str) -> list[NormalizedLot]:
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
            cadastres = [normalize_cadastral_number(item) for item in CADASTRAL_RE.findall(f"{title} {description}")]
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
            lots.append(NormalizedLot(
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
                    "listing_fingerprint": sha256(json.dumps({
                        "title": title,
                        "description": description,
                        "start_price": start_price,
                        "current_price": current_price,
                        "photos": photos,
                    }, ensure_ascii=False, sort_keys=True).encode()).hexdigest(),
                },
            ))
        return lots

    @staticmethod
    def _matching_lot_url(html: str, cadastral_number: str) -> str | None:
        soup = BeautifulSoup(html, "html.parser")
        expected = normalize_cadastral_number(cadastral_number)
        for article in soup.select("article"):
            observed = {normalize_cadastral_number(item) for item in CADASTRAL_RE.findall(article.get_text(" "))}
            if expected not in observed:
                continue
            for anchor in article.select('a[href*="/lot/"]'):
                candidate = urljoin(BASE_URL, str(anchor.get("href") or ""))
                parsed = urlparse(candidate)
                if re.fullmatch(r"/lot/\d+/?", parsed.path):
                    return urldefrag(candidate).url
        return None

    @staticmethod
    def parse_lot_page(html: str, page_url: str) -> TorgiRussiaDetails:
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
                value = value[len(prefix):].strip()
            if label and value:
                labels[label] = value

        def labelled(*needles: str) -> str | None:
            return next((value for key, value in labels.items() if any(needle in key for needle in needles)), None)

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

        return TorgiRussiaDetails(
            torgi_russia_url=urldefrag(page_url).url,
            gis_torgi_url=gis_torgi_url,
            etp_url=etp_url,
            image_urls=list(dict.fromkeys(image_urls)),
            procedure_number=(
                match.group(0) if (match := re.search(r"\b[A-Z0-9]{7,12}-\d{4}-\d{4}-\d\b", soup.get_text(" "))) else None
            ),
            address=labelled("адрес", "местонахожд"),
            category=labelled("категор", "вид имущества"),
            application_start_at=parse_date(labelled("начало приема заявок", "начало приёма заявок")),
            application_deadline=parse_date(labelled(
                "конец приема заявок", "конец приёма заявок",
                "окончание приема заявок", "окончание приёма заявок",
            )),
            auction_at=parse_date(labelled(
                "конец приема ценовых предложений", "конец приёма ценовых предложений",
                "дата торгов", "дата аукцион", "проведен",
            )),
        )
