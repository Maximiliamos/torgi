import requests

import bankrotai.scrapers as public_sources
from bankrotai.scrapers import GorodTorgiClient, PublicRealEstateLot, _normalize_address_for_search


def test_build_tbankrot_query_uses_region_context_without_name_error() -> None:
    client = GorodTorgiClient("ivanovo", enrich_details=False, resolve_tbankrot=False)
    lot = PublicRealEstateLot(
        source="gorod-torgi.ru",
        category="real_estate",
        published_at="15 апреля 2026",
        asset_type="Квартира",
        status="Опубликовано",
        price="1 000 000",
        title="Квартира",
        location="г. Иваново, ул. Ленина, д. 1",
        url="https://example.com/lot",
        reference_url="https://example.com/ref",
        source_label="primary",
    )

    query = client._build_tbankrot_query(lot)

    assert "Иван" in query


def test_normalize_address_for_search_does_not_force_yaroslavl() -> None:
    query = _normalize_address_for_search("г. Иваново, ул. Ленина, д. 1", "ivanovo")

    assert "Ярослав" not in query
    assert "Иван" in query


def test_iter_category_pages_uses_pager_from_first_page_only(monkeypatch) -> None:
    client = GorodTorgiClient("tula", enrich_details=False, resolve_tbankrot=False)
    first_page_url = "https://tula.gorod-torgi.ru/cat/realizaciya-imuschestva-dolzhnikov"
    seen_urls: list[str] = []
    html = """
    <html>
      <body>
        <a href="/cat/realizaciya-imuschestva-dolzhnikov/p-2">2</a>
        <a href="/cat/realizaciya-imuschestva-dolzhnikov/p-7">>></a>
      </body>
    </html>
    """

    monkeypatch.setattr(client, "_get_html", lambda url: seen_urls.append(url) or html)

    pages = client._iter_category_pages("realizaciya-imuschestva-dolzhnikov")

    assert seen_urls == [first_page_url]
    assert pages == [
        first_page_url,
        f"{first_page_url}/p-2",
        f"{first_page_url}/p-3",
        f"{first_page_url}/p-4",
        f"{first_page_url}/p-5",
        f"{first_page_url}/p-6",
        f"{first_page_url}/p-7",
    ]


def test_fetch_listing_lots_skips_failed_pages(monkeypatch) -> None:
    client = GorodTorgiClient("tula", enrich_details=False, resolve_tbankrot=False)
    lot = PublicRealEstateLot(
        source="gorod-torgi.ru",
        category="real_estate",
        published_at="15 апреля 2026",
        asset_type="Квартира",
        status="Опубликовано",
        price="1 000 000",
        title="Квартира",
        location="г. Тула, ул. Ленина, д. 1",
        url="https://example.com/lot/1",
        reference_url="https://example.com/ref/1",
        source_label="primary",
    )

    monkeypatch.setattr(public_sources, "DEFAULT_CATEGORY_SLUGS", ("real_estate",))
    monkeypatch.setattr(client, "_iter_category_pages", lambda _category_slug: ["page-1", "page-2"])

    def fake_parse_page(url: str, _category_slug: str) -> list[PublicRealEstateLot]:
        if url == "page-2":
            raise requests.RequestException("slow page")
        return [lot]

    monkeypatch.setattr(client, "_parse_page", fake_parse_page)

    lots = client.fetch_listing_lots()

    assert [item.url for item in lots] == ["https://example.com/lot/1"]
