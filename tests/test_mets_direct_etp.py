"""Public M-ETS card regression tests; all HTTP is mocked (no live scrape in CI)."""
from __future__ import annotations

import pytest

from bankrotai.connectors.registry.mets_public import (
    APARTMENTS_URL,
    DETAIL_MIRROR,
    MetsEvidenceError,
    MetsPublicPilot,
    discover_lot_urls,
    parse_mets_lot,
)

URL = "https://m-ets.ru/240512-1"
MIRROR_URL = f"{DETAIL_MIRROR}/240512-1"
CATALOGUE = """
<html><a href="/240512-1">Здание в Липецке</a>
<a href="https://m-ets.ru/240512-1">Repeat</a>
<a href="https://evil.example/111111-1">Not an ETP</a>
<a href="/238380-1">Квартира в Москве</a>
</html>
"""
DETAIL = """
<html lang="ru"><h1>240512-МЭТС</h1>
<h2>Здание в Липецке, 1240,8 кв.м.</h2>
<div>Лот 1</div>
<div>Идентификационный номер: 240512-МЭТС-1</div>
<div>22 500 000 ₽ Начальная цена</div>
<div>Объявленные торги</div>
<div>Начало приема заявок: 26.10.2026 09:00</div>
<div>Окончание приема заявок: 01.12.2026 15:00</div>
<div>Здание, площадь 1240,8 кв.м. Кадастровый номер: 48:20:0021101:37</div>
</html>
"""
DETAIL_PERIOD = """
<html><h1>238380-МЭТС</h1><h2>Квартира в Москве</h2>
<div>Идентификационный номер: 238380-МЭТС-1</div>
<div>3\u00a0436\u00a0617,64 ₽ Текущая цена</div>
<div>Торги в стадии приема заявок</div>
<div>Окончание приема заявок на периоде: 28.10.2026 12:00</div>
<div>Кадастровый номер: 77:09:0004006:11148</div></html>
"""


def test_discovery_only_allows_auction_lot_paths_on_official_host():
    assert discover_lot_urls(CATALOGUE) == [
        URL, "https://m-ets.ru/238380-1",
    ]
    assert discover_lot_urls(CATALOGUE, limit=1) == [URL]
    with pytest.raises(ValueError):
        discover_lot_urls(CATALOGUE, limit=1000)


def test_direct_etp_detail_price_status_identity_and_cadastre():
    lot = parse_mets_lot(DETAIL, URL)
    assert lot.external_id == "240512-1"
    assert lot.procedure_number == "240512-МЭТС"
    assert lot.title.startswith("Здание в Липецке")
    assert lot.cadastral_number == "48:20:0021101:37"
    assert lot.start_price == 22_500_000
    assert lot.current_price is None
    assert lot.auction_status == "scheduled"
    assert lot.application_deadline.isoformat() == "2026-12-01T15:00:00"
    assert lot.source_system == "m-ets.ru"
    assert lot.raw_data["evidence_scope"] == "pilot_non_exhaustive"


def test_period_is_not_the_final_application_deadline():
    lot = parse_mets_lot(
        DETAIL_PERIOD, "https://m-ets.ru/238380-1"
    )
    assert lot.current_price == 3436617.64
    assert lot.start_price is None
    assert lot.auction_status == "active"
    assert lot.application_deadline is None
    assert lot.raw_data["period_deadline"] == "2026-10-28T12:00:00"


@pytest.mark.parametrize("bad_url", [
    "http://m-ets.ru/240512-1",
    "https://evil.example/240512-1",
    "https://m-ets.ru/240512-1?q=secret",
])
def test_never_fetch_external_or_query_urls(bad_url):
    with pytest.raises(MetsEvidenceError):
        parse_mets_lot(DETAIL, bad_url)


def test_fail_closed_when_lot_identity_does_not_match_page():
    with pytest.raises(MetsEvidenceError, match="identifier"):
        parse_mets_lot(DETAIL, "https://m-ets.ru/240513-1")


class Response:
    def __init__(self, html, status_code=200):
        self.text = html
        self.content = html.encode("utf-8")
        self.status_code = status_code
        self.headers = {"Content-Type": "text/html"}

    def raise_for_status(self):
        return None


class SessionStub:
    def __init__(self, *, redirect=False):
        self.urls = []
        self.redirect = redirect

    def get(self, url, *, timeout, allow_redirects, headers):
        assert allow_redirects is False
        self.urls.append(url)
        if self.redirect:
            return Response("", status_code=302)
        if url == APARTMENTS_URL:
            return Response(CATALOGUE)
        if url == MIRROR_URL:
            return Response(DETAIL)
        if url == f"{DETAIL_MIRROR}/238380-1":
            return Response("<html><h2>Broken card</h2></html>")
        raise AssertionError(f"Unexpected requested URL: {url}")


def test_pilot_is_bounded_and_never_claims_complete_coverage(monkeypatch):
    import bankrotai.connectors.registry.mets_public as mod

    monkeypatch.setattr(mod.time, "sleep", lambda _: None)
    session = SessionStub()
    result = MetsPublicPilot(session=session).preview(max_lots=2)
    assert result["discovered"] == 2
    assert result["parsed"] == 1
    assert result["failed"] == 1
    assert result["complete_source_inventory"] is False
    assert result["safe_for_archival"] is False
    assert result["written_to_database"] is False
    assert session.urls == [
        APARTMENTS_URL, MIRROR_URL, f"{DETAIL_MIRROR}/238380-1",
    ]


def test_redirect_refused_without_following():
    with pytest.raises(MetsEvidenceError, match="Redirect"):
        MetsPublicPilot(session=SessionStub(redirect=True)).preview()


def test_registry_connector_fetches_exact_etp_lot_but_blocks_sync():
    import asyncio

    from bankrotai.connectors.base import ConnectorCapabilityError
    from bankrotai.connectors.registry import connector_registry
    from bankrotai.connectors.registry.mets_public import MetsConnector

    connector = MetsConnector(session=SessionStub())
    assert connector_registry.create("m-ets.ru").source_id == "m-ets.ru"
    got = asyncio.run(connector.fetch_lot("240512-1"))
    assert got.start_price == 22_500_000
    assert got.external_id == "240512-1"
    with pytest.raises(ConnectorCapabilityError):
        asyncio.run(connector.search({}))



def test_saved_browser_snapshot_summary_is_parsed_even_after_large_header():
    """Model the actual m-ets.ru DOM: several thousand chars of modal chrome."""
    html = (
        '<html><head><meta itemprop="sku" content="242979-МЭТС-1"></head>'
        '<body><div>' + 'Настройки авторизации. ' * 260 + '</div>'
        '<h1>242979-МЭТС</h1>'
        '<h2 class="lot-title">2-комнатная квартира, г Рязань, ул Бирюзова<span class="hidden" ellipse=""><span>еще</span></span></h2>'
        '<div class="lot-regnumber">Идентификационный номер: 242979-МЭТС-1</div>'
        '<div class="lot-cost-item price"><div class="value">3 224 286 ₽'
        '</div><div class="title">Начальная цена</div></div>'
        '<div class="lot-cost-item zadat"><div class="value">'
        '644 857,20 ₽</div><div class="title">Размер задатка</div></div>'
        '<div class="lot-cost-item status"><div class="lot-status-name">'
        'Объявленные торги</div></div>'
        '<div class="lot-cost-item date"><div class="title">'
        'Начало приема заявок:</div><div class="value">02.11.2026 09:00</div></div>'
        '<div class="lot-cost-item date"><div class="title">'
        'Окончание приема заявок:</div><div class="value">04.12.2026 09:00</div></div>'
        '<span itemprop="description">Двухкомнатная квартира площадью 50,10 кв.м.'
        ' Кадастровый номер: 62:29:0020001:1008, находится в залоге.</span>'
        '<a class="card-item" href="/100001-1">Рекламный лот</a>'
        '</body></html>'
    )
    lot = parse_mets_lot(html, "https://m-ets.ru/242979-1")
    assert lot.external_id == "242979-1"
    assert lot.title == "2-комнатная квартира, г Рязань, ул Бирюзова"
    assert lot.start_price == 3224286
    assert lot.current_price is None
    assert lot.auction_status == "scheduled"
    assert lot.application_deadline.isoformat() == "2026-12-04T09:00:00"
    assert lot.cadastral_number == "62:29:0020001:1008"
    assert lot.raw_data["mentions_pledge"] is True
    assert lot.raw_data["deposit_rub"] == 644857.20
    assert lot.raw_data["complete_search_pagination"] is False
    assert "Рекламный лот" not in lot.description


def test_multi_lot_procedure_scopes_fields_to_url_sku():
    def block(number: int, title: str, price: str, cadastral: str) -> str:
        return (
            '<div class="lot-container">'
            f'<h2 class="lot-title">{title}<span ellipse="" class="hidden">еще</span></h2>'
            f'<meta itemprop="sku" content="242937-МЭТС-{number}">'
            f'<div>Идентификационный номер: 242937-МЭТС-{number}</div>'
            f'<span itemprop="description">Кадастровый номер: {cadastral}</span>'
            '<div class="lot-cost-item price">'
            f'<div class="value">{price} ₽</div><div class="title">Начальная цена</div></div>'
            '<div class="lot-cost-item status"><div class="lot-status-name">'
            'Торги в стадии приема заявок</div></div></div>'
        )

    html = "<html><body>" + "".join((
        block(1, "Земельный участок 6138 кв.м.", "1 278 299,88", "36:25:6945016:1967"),
        block(2, "Земельный участок 171 кв.м.", "35 612,46", "36:25:6945016:1968"),
        block(3, "Земельный участок 159 кв.м.", "33 113,34", "36:25:6945016:1969"),
    )) + "</body></html>"

    lot = parse_mets_lot(html, "https://m-ets.ru/242937-2")

    assert lot.external_id == "242937-2"
    assert lot.title == "Земельный участок 171 кв.м."
    assert lot.start_price == 35612.46
    assert lot.cadastral_number == "36:25:6945016:1968"
    assert "6138" not in lot.description


def test_homepage_event_feed_never_counts_as_full_search_result():
    html = (
        '<html><head><meta property="og:url" content="https://m-ets.ru/">'
        '</head><body><a href="/242979-1">Свежие торги</a></body></html>'
    )
    with pytest.raises(MetsEvidenceError, match="Homepage"):
        discover_lot_urls(html)


def test_lot_meta_sku_disagreement_fails_closed():
    html = DETAIL.replace(
        '<html lang="ru">', '<html lang="ru"><meta itemprop="sku" content="240513-МЭТС-1">'
    )
    with pytest.raises(MetsEvidenceError, match="SKU"):
        parse_mets_lot(html, URL)


def test_mets_provider_throttling_is_not_retried(monkeypatch):
    import bankrotai.connectors.registry.mets_public as mod

    class ThrottledSession:
        def __init__(self):
            self.calls = 0

        def get(self, url, **kwargs):
            self.calls += 1
            assert kwargs["allow_redirects"] is False
            return Response("", status_code=429)

    monkeypatch.setattr(mod.time, "sleep", lambda _: None)
    session = ThrottledSession()
    with pytest.raises(MetsEvidenceError, match="provider access explicitly refused"):
        MetsPublicPilot(session=session)._get(URL)
    assert session.calls == 1



def test_official_meetings_detail_domain_keeps_exact_trade_lot_identity():
    mirror_url = "https://meetings.m-ets.ru/240512-1"
    lot = parse_mets_lot(DETAIL, mirror_url)
    assert lot.external_id == "240512-1"
    assert lot.lot_url == URL
    assert lot.source_system == "m-ets.ru"
    assert discover_lot_urls(
        '<a href="https://meetings.m-ets.ru/240512-1">official mirror</a>'
    ) == [URL]


def test_canonical_detail_fetch_uses_official_mirror_without_legacy_403(monkeypatch):
    import bankrotai.connectors.registry.mets_public as mod

    class MirrorOnlySession:
        def __init__(self):
            self.calls = []

        def get(self, url, *, timeout, allow_redirects, headers):
            assert allow_redirects is False
            self.calls.append(url)
            assert url == MIRROR_URL
            return Response(DETAIL)

    monkeypatch.setattr(mod.time, "sleep", lambda _: None)
    session = MirrorOnlySession()
    starts = []
    pilot = MetsPublicPilot(session=session)
    html = pilot._get(URL, before_request=starts.append)
    lot = parse_mets_lot(html, URL)
    assert lot.external_id == "240512-1"
    assert lot.start_price == 22_500_000
    assert session.calls == [MIRROR_URL]
    assert starts == session.calls


@pytest.mark.parametrize("unsafe_location", [
    "http://meetings.m-ets.ru/240512-1",  # TLS downgrade
    "https://evil.example/240512-1",  # arbitrary host
    "https://meetings.m-ets.ru/240512-2",  # different lot
    "https://meetings.m-ets.ru/240512-1?next=1",  # query
    "https://meetings.m-ets.ru@evil.example/240512-1",  # userinfo trick
    "/240512-1",  # not an independently verified absolute HTTPS location
])
def test_any_redirect_from_official_detail_mirror_is_rejected(monkeypatch, unsafe_location):
    import bankrotai.connectors.registry.mets_public as mod

    class RedirectSession:
        def __init__(self):
            self.calls = 0

        def get(self, url, **kwargs):
            self.calls += 1
            response = Response("", status_code=302)
            response.headers["Location"] = unsafe_location
            return response

    monkeypatch.setattr(mod.time, "sleep", lambda _: None)
    session = RedirectSession()
    with pytest.raises(MetsEvidenceError, match="Redirect"):
        MetsPublicPilot(session=session)._get(URL)
    assert session.calls == 1


def test_mirror_does_not_follow_even_self_redirect(monkeypatch):
    import bankrotai.connectors.registry.mets_public as mod

    class MultiHopSession:
        def __init__(self):
            self.calls = 0

        def get(self, url, **kwargs):
            self.calls += 1
            response = Response("", status_code=302)
            response.headers["Location"] = "https://meetings.m-ets.ru/240512-1"
            return response

    monkeypatch.setattr(mod.time, "sleep", lambda _: None)
    session = MultiHopSession()
    with pytest.raises(MetsEvidenceError, match="Redirect"):
        MetsPublicPilot(session=session)._get(URL)
    assert session.calls == 1


def test_mirror_http_429_is_not_retried(monkeypatch):
    import bankrotai.connectors.registry.mets_public as mod

    class MirrorThrottleSession:
        def __init__(self):
            self.calls = 0

        def get(self, url, **kwargs):
            self.calls += 1
            assert url == MIRROR_URL
            return Response("", status_code=429)

    monkeypatch.setattr(mod.time, "sleep", lambda _: None)
    session = MirrorThrottleSession()
    with pytest.raises(MetsEvidenceError, match="provider access explicitly refused"):
        MetsPublicPilot(session=session)._get(URL)
    assert session.calls == 1  # never re-request the 429 response
