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
        })

    def find_by_cadastral_numbers(self, values: list[str]) -> TorgiRussiaDetails | None:
        cadastral_numbers = [normalize_cadastral_number(value) for value in values if value]
        for cadastral_number in dict.fromkeys(cadastral_numbers):
            response = self.session.get(
                urljoin(BASE_URL, "/search"),
                params={"search": cadastral_number},
                timeout=self.timeout,
            )
            response.raise_for_status()
            lot_url = self._matching_lot_url(response.text, cadastral_number)
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
        params = {
            "categories[0]": filters.category_id,
            "history_only": "1" if filters.history_only else "0",
            "page": max(1, int(filters.page)),
        }
        response = self.session.get(urljoin(BASE_URL, "/search"), params=params, timeout=self.timeout)
        response.raise_for_status()
        lots = self.parse_search_page(response.text, response.url or urljoin(BASE_URL, "/search"))
        soup = BeautifulSoup(response.text, "html.parser")
        next_link = soup.select_one("ul.pagination a[rel='next']")
        last_page = max(
            (
                int(text)
                for item in soup.select("ul.pagination .page-link")
                if (text := item.get_text(" ", strip=True)).isdigit()
            ),
            default=filters.page,
        )
        return lots, {
            "source": "torgi-russia.ru",
            "page": filters.page,
            "loaded": len(lots),
            "has_more": next_link is not None,
            "total_pages": last_page,
            "raw_endpoint": response.url,
        }

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
