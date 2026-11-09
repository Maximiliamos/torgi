from __future__ import annotations

from datetime import datetime
import re
from typing import Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from bankrotai.scraper_contracts import parse_money


ENDED_DETAIL_MARKERS = (
    "торги завершены",
    "торги не состоялись",
    "торги состоялись",
    "прием заявок завершен",
    "приём заявок завершен",
    "лот снят с торгов",
)
ACTIVE_DETAIL_MARKERS = (
    "текущая цена",
    "идут торги",
    "прием заявок",
    "приём заявок",
)


def parse_tbankrot_detail_html(html: str, page_url: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "html.parser")
    page_text = soup.get_text(" ", strip=True).casefold()
    auction_status = (
        "closed"
        if any(token in page_text for token in ENDED_DETAIL_MARKERS)
        else "active"
        if any(token in page_text for token in ACTIVE_DETAIL_MARKERS)
        else "unknown"
    )

    def labelled_price(selector: str, *labels: str) -> float | None:
        node = soup.select_one(selector)
        if node is not None:
            value = parse_money(node.get_text(" ", strip=True))
            if value is not None:
                return value
        for block in soup.select(".price_info > div, .price_info .price, .lot_prices > div"):
            text_value = block.get_text(" ", strip=True)
            lowered = text_value.casefold()
            if any(label in lowered for label in labels):
                return parse_money(text_value)
        return None

    image_urls: list[str] = []
    for node in soup.select("img, [style*='background-image']"):
        candidates = [node.get(key) for key in ("data-src", "data-lazy-src", "src")]
        style = str(node.get("style") or "")
        candidates.extend(re.findall(r"url\(['\"]?([^)'\"\"]+)", style, flags=re.IGNORECASE))
        for candidate in candidates:
            if not candidate:
                continue
            url = urljoin(page_url, str(candidate).strip())
            lowered = url.casefold()
            classes = " ".join(node.get("class") or []).casefold()
            if (
                "/img/blur/" in lowered
                or "only_biz" in classes
                or not lowered.startswith(("http://", "https://"))
            ):
                continue
            if url not in image_urls:
                image_urls.append(url)

    def labelled_datetime(*labels: str) -> datetime | None:
        text_value = soup.get_text(" ", strip=True)
        joined = "|".join(re.escape(label) for label in labels)
        match = re.search(
            rf"(?:{joined})\s*[:\-]?\s*(\d{{1,2}}\.\d{{1,2}}\.\d{{2,4}})"
            r"(?:\s+(?:в\s*)?(\d{1,2}:\d{2}))?",
            text_value,
            re.IGNORECASE,
        )
        if not match:
            return None
        raw_date = match.group(1)
        raw_time = match.group(2) or "00:00"
        for fmt in ("%d.%m.%y %H:%M", "%d.%m.%Y %H:%M"):
            try:
                return datetime.strptime(f"{raw_date} {raw_time}", fmt)
            except ValueError:
                pass
        return None

    return {
        "start_price": labelled_price(
            ".price_info .start_price .sum",
            "начальная цена",
            "стартовая цена",
        ),
        "current_price": labelled_price(
            ".price_info .cur_price .green, .price_info .cur_price .semibold",
            "текущая цена",
        ),
        "minimum_price": labelled_price(
            ".price_info .min_price .semibold",
            "минимальная цена",
        ),
        "application_deadline": labelled_datetime(
            "дата окончания заявок",
            "дата окончания приема заявок",
            "дата окончания приёма заявок",
            "окончание приема заявок",
            "окончание приёма заявок",
            "прием заявок до",
            "приём заявок до",
        ),
        "auction_at": labelled_datetime(
            "дата проведения",
            "дата торгов",
            "начало торгов",
        ),
        "image_urls": image_urls,
        "auction_status": auction_status,
        "detail_url": page_url,
    }
