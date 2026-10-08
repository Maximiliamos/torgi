"""Public RAD / ЛОТ-ОНЛАЙН catalogue client (BAT-308).

Behavior-preserving extraction of the original scraper implementation.
Import through bankrotai.scrapers remains supported and uses identical classes.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import replace
from datetime import datetime
from typing import Any, Callable
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from bankrotai.domain import NormalizedLot
from bankrotai.extractors import extract_address, extract_area, extract_cadastral_numbers
from bankrotai.logic import classify_category
from bankrotai.scraper_contracts import LotOnlineSearchFilters, parse_money


class LotOnlineClientError(RuntimeError):
    """User-facing error raised when the public RAD catalogue cannot be queried."""


class LotOnlineClient:
    BASE_URL = "https://catalog.lot-online.ru"
    SEARCH_ENDPOINT = f"{BASE_URL}/index.php"
    RETRY_DELAYS_SECONDS = (1, 2, 4, 8)
    RETRY_STATUS_CODES = frozenset({429, 502, 503, 504})
    DEFAULT_CATEGORY_ID = "1"
    CATEGORY_LABELS = {
        "1": "Недвижимое имущество",
        "9876": "Все категории",
    }
    # Values are public CS-Cart feature identifiers exposed by the catalogue.
    REGION_FEATURES = {
        "24392": "Ярославская область",
    }
    REGION_NAME_TO_FEATURE = {label.lower(): code for code, label in REGION_FEATURES.items()}

    def __init__(
        self,
        *,
        timeout: tuple[float, float] | float = (10, 60),
        session: requests.Session | None = None,
        diagnostics: bool = False,
    ) -> None:
        self.timeout = timeout
        self.diagnostics = diagnostics
        self.session = session or requests.Session()
        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/134.0 Safari/537.36 BankrotAI/1.0"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.7",
        })

    def _get_with_retry(self, url: str, *, params: dict[str, str] | None = None):
        for attempt in range(len(self.RETRY_DELAYS_SECONDS) + 1):
            try:
                response = self.session.get(url, params=params, timeout=self.timeout)
                status_code = int(getattr(response, "status_code", 0) or 0)
                if status_code in self.RETRY_STATUS_CODES:
                    if attempt >= len(self.RETRY_DELAYS_SECONDS):
                        response.raise_for_status()
                        return response
                    time.sleep(self.RETRY_DELAYS_SECONDS[attempt])
                    continue
                response.raise_for_status()
                return response
            except (
                requests.Timeout,
                requests.ConnectionError,
                requests.exceptions.ChunkedEncodingError,
            ):
                if attempt >= len(self.RETRY_DELAYS_SECONDS):
                    raise
                time.sleep(self.RETRY_DELAYS_SECONDS[attempt])
        raise RuntimeError("Lot Online request retry loop exhausted unexpectedly")

    @classmethod
    def _region_feature_hash(cls, value: str | None) -> str | None:
        if not value:
            return None
        normalized = str(value).strip()
        if normalized.startswith("171-"):
            return normalized
        if normalized in {"76", "yaroslavl"}:
            normalized = "Ярославская область"
        feature = cls.REGION_NAME_TO_FEATURE.get(normalized.lower(), normalized)
        if not feature.isdigit():
            raise ValueError(f"Неизвестный регион ЛОТ-ОНЛАЙН: {value}")
        return f"171-{feature}"

    def _build_query_params(self, filters: LotOnlineSearchFilters) -> dict[str, str]:
        archive_mode = str(filters.archive_mode or "false").lower()
        if archive_mode not in {"false", "true", "all"}:
            raise ValueError("archive_mode должен быть false, true или all")
        page_size = max(12, min(int(filters.page_size or 96), 96))
        params = {
            "dispatch": "categories.view",
            "category_id": str(filters.category_id or self.DEFAULT_CATEGORY_ID),
            "filter_fields[is_aggregator]": "all",
            "filter_fields[is_archive]": archive_mode,
            "items_per_page": str(page_size),
            "page": str(max(1, int(filters.page or 1))),
        }
        if filters.search_text.strip():
            params["q"] = filters.search_text.strip()
        region_hash = self._region_feature_hash(filters.region_feature)
        if region_hash:
            params["features_hash"] = region_hash
        return params

    def _prepare_url(self, params: dict[str, str]) -> str:
        return f"{self.SEARCH_ENDPOINT}?{urlencode(params)}"

    @staticmethod
    def _detail_text(node: Any) -> str:
        value = node.get_text(" ", strip=True) if node is not None else ""
        value = re.sub(r"\s+", " ", value).strip()
        # Some catalogue responses have historically contained UTF-8 text
        # decoded once as latin-1. Repair only when the mojibake signature is
        # present; normal Russian text is left untouched.
        if ("Ð" in value or "Ñ" in value) and not re.search(r"[А-Яа-я]", value):
            try:
                value = value.encode("latin-1").decode("utf-8")
            except (UnicodeEncodeError, UnicodeDecodeError):
                pass
        return value

    def fetch_detail_fields(self, url: str) -> dict[str, Any]:
        """Fetch structured address/cadastre fields absent from listing cards."""
        try:
            response = self._get_with_retry(url)
        except requests.RequestException as exc:
            raise LotOnlineClientError(f"Не удалось загрузить карточку ЛОТ-ОНЛАЙН: {exc}") from exc

        soup = BeautifulSoup(response.content, "lxml")
        labelled: dict[str, str] = {}
        for container in soup.select("dl > div"):
            label_node = container.find("dt")
            value_node = container.find("dd")
            label = self._detail_text(label_node).casefold()
            value = self._detail_text(value_node)
            if label and value:
                labelled[label] = value
        for group in soup.select(".ty-control-group"):
            label = self._detail_text(group.select_one(".ty-control-group__label")).casefold()
            value = self._detail_text(group.select_one(".ty-control-group__item"))
            if label and value:
                labelled[label] = value
        for row in soup.select(".row-request-date, .row-auction-date"):
            label = self._detail_text(row.select_one("dt, .label")).casefold()
            value = self._detail_text(row.select_one("dd"))
            if label and value:
                labelled[label] = value

        def labelled_value(*needles: str) -> str | None:
            return next((value for label, value in labelled.items() if any(needle in label for needle in needles)), None)

        def parse_datetime(value: str | None) -> datetime | None:
            if not value:
                return None
            match = re.search(r"\d{2}\.\d{2}\.\d{4}(?:\s+(?:в\s*)?\d{1,2}:\d{2})?", value, re.I)
            if not match:
                return None
            normalized = re.sub(r"\s+в\s*", " ", match.group(0), flags=re.I)
            for pattern in ("%d.%m.%Y %H:%M", "%d.%m.%Y"):
                try:
                    return datetime.strptime(normalized, pattern)
                except ValueError:
                    continue
            return None

        def parse_last_datetime(value: str | None) -> datetime | None:
            if not value:
                return None
            matches = re.findall(r"\d{2}\.\d{2}\.\d{4}(?:\s+(?:в\s*)?\d{1,2}:\d{2})?", value, re.I)
            return parse_datetime(matches[-1]) if matches else None

        descriptions = [
            self._detail_text(node)
            for node in soup.select(".ty-product__full-description")
        ]
        description = max((value for value in descriptions if value), key=len, default="")
        page_text = " ".join((description, self._detail_text(soup.title)))
        # A generic detail-page "Адрес" may belong to the seller or
        # auction venue. The full lot description identifies the property.
        address = extract_address(description) or labelled.get("адрес") or extract_address(page_text)
        cadastral_numbers = extract_cadastral_numbers(page_text)
        if not cadastral_numbers:
            for group in soup.select(".ty-control-group"):
                label = self._detail_text(group.select_one(".ty-control-group__label")).casefold()
                if "кадастров" in label:
                    cadastral_numbers.extend(extract_cadastral_numbers(self._detail_text(group)))
        return {
            "address": address,
            "cadastral_numbers": list(dict.fromkeys(cadastral_numbers)),
            "description": description or None,
            "application_start_at": parse_datetime(labelled_value("начал", "период приема заявок", "период приёма заявок")),
            "application_deadline": parse_last_datetime(labelled_value("окончан", "период приема заявок", "период приёма заявок")),
            "auction_at": parse_last_datetime(labelled_value("дата торгов", "дата аукцион", "аукцион", "проведен")),
            "url": response.url or url,
        }

    def search_lots(self, filters: LotOnlineSearchFilters) -> tuple[list[NormalizedLot], dict[str, Any]]:
        params = self._build_query_params(filters)
        try:
            response = self._get_with_retry(self.SEARCH_ENDPOINT, params=params)
        except requests.RequestException as exc:
            raise LotOnlineClientError(
                "Не удалось подключиться к catalog.lot-online.ru. "
                f"Проверьте сеть или попробуйте позже. Детали: {exc}"
            ) from exc

        lots, page_meta = self._parse_listing_html(response.content, filters=filters)
        lots = [lot for lot in lots if is_sale_real_estate_lot(lot)]
        page = max(1, int(filters.page or 1))
        page_size = max(12, min(int(filters.page_size or 96), 96))
        meta = {
            "source": "lot-online.ru",
            "mode": "page",
            "page": page,
            "page_size": page_size,
            "items_on_page": len(lots),
            "loaded": len(lots),
            "has_more": page_meta["has_more"],
            "total_pages": page_meta["total_pages"],
            "raw_endpoint": response.url or self._prepare_url(params),
            "raw_params": params,
            "warnings": [],
        }
        return lots, meta

    def _parse_listing_html(
        self,
        html: str | bytes,
        *,
        filters: LotOnlineSearchFilters,
    ) -> tuple[list[NormalizedLot], dict[str, Any]]:
        soup = BeautifulSoup(html, "lxml")
        lots: list[NormalizedLot] = []
        seen: set[str] = set()
        region_feature = str(filters.region_feature or "").removeprefix("171-")
        region_name = self.REGION_FEATURES.get(region_feature)
        status = "closed" if filters.archive_mode == "true" else ("active" if filters.archive_mode == "false" else "unknown")

        for card in soup.select(".ty-grid-list__item"):
            title_link = card.select_one("a.product-title[href*='products.view']")
            if title_link is None:
                continue
            href = urljoin(self.BASE_URL, str(title_link.get("href") or ""))
            product_id_match = re.search(r"(?:[?&]product_id=)(\d+)", href)
            if not product_id_match:
                product_input = card.select_one("input[name*='[product_id]']")
                product_id = str(product_input.get("value") or "") if product_input else ""
            else:
                product_id = product_id_match.group(1)
            if not product_id or product_id in seen:
                continue
            seen.add(product_id)

            title = title_link.get("title") or title_link.get_text(" ", strip=True)
            title = re.sub(r"\s+", " ", str(title)).strip()
            price_node = card.select_one(".ty-price-num")
            price_text = price_node.get_text(" ", strip=True) if price_node else ""
            price = parse_money(price_text)
            code_node = card.select_one(".ty-grid-list__product-code")
            procedure_number = code_node.get_text(" ", strip=True) if code_node else None
            cadastral_numbers = extract_cadastral_numbers(title)
            image_urls: list[str] = []
            for image_node in card.select("img.ty-pict, img.cm-image"):
                image_ref = str(image_node.get("src") or image_node.get("data-src") or "").strip()
                if not image_ref or image_ref.startswith("data:"):
                    continue
                image_url = urljoin(self.BASE_URL, image_ref)
                if image_url not in image_urls:
                    image_urls.append(image_url)

            raw_data = {
                "source": "lot_online_search",
                "product_id": product_id,
                "procedure_number": procedure_number,
                "price_text": price_text,
                "category_id": str(filters.category_id),
                "category_name": self.CATEGORY_LABELS.get(str(filters.category_id)),
                "region_feature": region_feature or None,
                "region_name": region_name,
                "archive_mode": filters.archive_mode,
                "cadastral_numbers": cadastral_numbers,
                "image_url": image_urls[0] if image_urls else None,
                "image_urls": image_urls,
            }
            raw_data["listing_fingerprint"] = hashlib.sha256(json.dumps(
                {
                    "title": title,
                    "price": price,
                    "procedure_number": procedure_number,
                    "image_urls": image_urls,
                    "archive_mode": filters.archive_mode,
                },
                ensure_ascii=False,
                sort_keys=True,
            ).encode("utf-8")).hexdigest()
            lots.append(NormalizedLot(
                external_id=f"lot-online:{product_id}",
                source="lot-online",
                source_system="lot-online.ru",
                title=title[:500],
                description=title[:5000],
                category=classify_category(title, title),
                region_slug="76" if region_feature == "24392" else region_feature or None,
                region_name=region_name,
                address=extract_address(title),
                cadastral_number=cadastral_numbers[0] if cadastral_numbers else None,
                vin=None,
                area=extract_area(title),
                start_price=price,
                current_price=price,
                auction_status=status,
                lot_url=href,
                source_url=href,
                detail_level="search",
                raw_data=raw_data,
                platform_name="РАД / ЛОТ-ОНЛАЙН",
                platform_code="rad",
                procedure_number=procedure_number,
            ))

        page = max(1, int(filters.page or 1))
        page_numbers = []
        has_next = False
        for link in soup.select(".ty-pagination a[href]"):
            href = str(link.get("href") or "")
            page_match = re.search(r"(?:[?&]page=)(\d+)", href)
            if page_match:
                target_page = int(page_match.group(1))
                page_numbers.append(target_page)
                if target_page > page:
                    has_next = True
        total_pages = max([page, *page_numbers])
        return lots, {"has_more": has_next, "total_pages": total_pages}

    def search_all_lots(
        self,
        filters: LotOnlineSearchFilters,
        *,
        max_items: int | None = 5000,
        max_pages: int = 500,
        progress_cb: Callable[[int, int | None, int], None] | None = None,
        page_cb: Callable[[list[NormalizedLot], dict[str, Any]], None] | None = None,
        stop_cb: Callable[[], bool] | None = None,
    ) -> tuple[list[NormalizedLot], dict[str, Any]]:
        all_lots: list[NormalizedLot] = []
        seen: set[str] = set()
        page_diagnostics: list[dict[str, Any]] = []
        last_meta: dict[str, Any] = {}
        stop_reason = "last_page"

        for page in range(max(1, filters.page), max_pages + 1):
            if stop_cb and stop_cb():
                stop_reason = "user_stopped"
                break
            page_filters = replace(filters, page=page)
            lots_on_page, page_meta = self.search_lots(page_filters)
            new_lots = [lot for lot in lots_on_page if lot.external_id not in seen]
            accepted_lots: list[NormalizedLot] = []
            for lot in new_lots:
                seen.add(lot.external_id)
                all_lots.append(lot)
                accepted_lots.append(lot)
                if max_items and len(all_lots) >= max_items:
                    stop_reason = "max_items"
                    break
            last_meta = dict(page_meta)
            last_meta.update({"mode": "all_pages", "pages_loaded": len(page_diagnostics) + 1, "loaded": len(all_lots)})
            page_diagnostics.append({
                "page": page,
                "items_on_page": len(lots_on_page),
                "new_unique": len(accepted_lots),
            })
            if page_cb and accepted_lots:
                page_cb(accepted_lots, last_meta)
            if progress_cb:
                progress_cb(page, page_meta.get("total_pages"), len(all_lots))
            if stop_reason == "max_items":
                break
            if not page_meta.get("has_more"):
                stop_reason = "last_page"
                break
            if lots_on_page and not new_lots:
                stop_reason = "duplicates_only"
                break
            time.sleep(0.2)
        else:
            stop_reason = "max_pages"

        meta = dict(last_meta)
        meta.update({
            "source": "lot-online.ru",
            "mode": "all_pages",
            "loaded": len(all_lots),
            "pages_loaded": len(page_diagnostics),
            "stop_reason": stop_reason,
            "page_diagnostics": page_diagnostics,
        })
        return all_lots, meta
