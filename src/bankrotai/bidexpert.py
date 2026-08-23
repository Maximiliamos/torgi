from __future__ import annotations

import re
from datetime import datetime
from typing import Any
from urllib.parse import urlencode

import requests
from bs4 import BeautifulSoup

from bankrotai.domain import NormalizedLot
from bankrotai.extractors import extract_cadastral_numbers
from bankrotai.logic import classify_category
from bankrotai.scraper_contracts import BidExpertSearchFilters, parse_money


class BidExpertClient:
    BASE_URL = "https://bidexpert.ru"
    SEARCH_ENDPOINT = f"{BASE_URL}/bids/"

    def __init__(self, *, timeout: tuple[float, float] | float = (10, 45), session: requests.Session | None = None) -> None:
        self.timeout = timeout
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": "BankrotAI/1.0 (+https://dezster.ru)", "Accept-Language": "ru-RU,ru;q=0.9"})

    def _params(self, filters: BidExpertSearchFilters) -> dict[str, str]:
        category = filters.category.strip().lower()
        if category not in {"realty", "land"}:
            raise ValueError("BidExpert category must be realty or land")
        params = {f"category-{category}": "1", "p": str(max(1, filters.page))}
        if filters.region:
            params["geo[0]"] = filters.region
        if filters.price_min is not None:
            params["minprice"] = str(int(filters.price_min))
        if filters.price_max is not None:
            params["maxprice"] = str(int(filters.price_max))
        return params

    def search_lots(self, filters: BidExpertSearchFilters) -> tuple[list[NormalizedLot], dict[str, Any]]:
        response = self.session.get(self.SEARCH_ENDPOINT, params=self._params(filters), timeout=self.timeout)
        response.raise_for_status()
        lots = self.parse_listing_html(response.text, filters=filters, endpoint=response.url)
        return lots, {"source": "bidexpert.ru", "page": filters.page, "total_pages": self._total_pages(response.text), "has_more": filters.page < self._total_pages(response.text), "raw_endpoint": response.url}

    def parse_listing_html(self, html: str, *, filters: BidExpertSearchFilters, endpoint: str = "fixture") -> list[NormalizedLot]:
        soup = BeautifulSoup(html, "lxml")
        result: list[NormalizedLot] = []
        for link in soup.select('a[href*="/bids/lot/?n="]'):
            href = link.get("href") or ""
            match = re.search(r"[?&]n=(\d+)", href)
            if not match:
                continue
            card = link.find_parent("div", class_=re.compile(r"lot|item|bid", re.I)) or link.parent
            if card is None:
                continue
            text = card.get_text(" ", strip=True)
            title_node = card.select_one(".title")
            title = title_node.get_text(" ", strip=True) if title_node else text
            # BidExpert's `category-realty=1` sample explicitly contains leases.
            if re.search(r"\bаренд[аы]\b|объект аренды", title, re.I):
                continue
            numbers = extract_cadastral_numbers(title)
            price_node = card.select_one(".start-price")
            deadline_node = card.select_one(".application-submit-end span")
            deadline = self._parse_moscow_datetime(deadline_node.get_text(" ", strip=True) if deadline_node else "")
            address_match = re.search(r"(?:по адресу|адрес[^:]*:)\s*([^.;]+)", title, re.I)
            result.append(NormalizedLot(
                external_id=f"bidexpert:{match.group(1)}", source="bidexpert", source_system="bidexpert.ru",
                title=title[:500], description=title[:5000], category=classify_category(title, title),
                region_slug=(numbers[0].split(":", 1)[0].zfill(2) if numbers else None), region_name=None,
                address=address_match.group(1).strip() if address_match else None,
                cadastral_number=numbers[0] if numbers else None, vin=None, area=None,
                start_price=parse_money(price_node.get_text(" ", strip=True) if price_node else None), current_price=None,
                auction_status="active", lot_url=href, source_url=href, detail_level="search",
                raw_data={"cadastral_numbers": numbers, "application_deadline": deadline.isoformat() if deadline else None, "raw_endpoint": endpoint, "source_category": filters.category},
                application_deadline=deadline,
            ))
        return result

    @staticmethod
    def _total_pages(html: str) -> int:
        match = re.search(r"Страница\s+\d+\s+из\s+(\d+)", BeautifulSoup(html, "lxml").get_text(" ", strip=True), re.I)
        return int(match.group(1)) if match else 1

    @staticmethod
    def _parse_moscow_datetime(value: str) -> datetime | None:
        match = re.search(r"(\d{2})-(\d{2})-(\d{4}),\s*(\d{2}):(\d{2})", value)
        if not match:
            return None
        return datetime(int(match.group(3)), int(match.group(2)), int(match.group(1)), int(match.group(4)), int(match.group(5)))
