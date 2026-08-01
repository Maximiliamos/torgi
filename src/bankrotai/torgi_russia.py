from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from urllib.parse import urldefrag, urljoin, urlparse

import requests
from bs4 import BeautifulSoup


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

    def as_dict(self) -> dict:
        return {
            "torgi_russia_url": self.torgi_russia_url,
            "gis_torgi_url": self.gis_torgi_url,
            "etp_url": self.etp_url,
            "torgi_russia_image_urls": list(self.image_urls),
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
        )
