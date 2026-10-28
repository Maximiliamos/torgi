"""Shared offline contract tests for all 20 ETP catalogue profiles.

No live HTTP: source access is checked together by scripts/probe_direct_etps.py.
"""
from __future__ import annotations

from urllib.parse import urlsplit

import pytest

from bankrotai.connectors.registry.direct_etp_catalogue import (
    EtpCatalogError, PROFILE_BY_ID, PROFILES, batch_probe,
    parse_catalogue, safe_url,
)


PER_LOT = """
<table><tr><th>№</th><th>Торги</th><th>№ лота</th>
<th>Лот</th><th>Начальная цена</th><th>Организатор</th>
<th>Окончание</th><th>Дата торгов</th><th>Статус</th>
</tr><tr>
<td><a href="/public/auctions/view/222/">0000456</a></td>
<td>Продажа недвижимости</td><td>1</td>
<td><a href="/public/auctions/lots/view/224/">Земельный участок
кадастровый номер 50:05:0010203:1234</a></td>
<td>2 500 000,25</td><td>Организатор</td>
<td>23.12.2026 10:00</td><td>24.12.2026 10:00</td>
<td>Прием заявок</td></tr></table>
"""
MULTI = """
<table><tr><th>Торги</th><th>Организатор</th><th>Должник</th>
</tr><tr><td><a href="/bankrot/trade_view.php?trade_nid=1234">71874-ОАОФ</a></td>
<td>Управляющий</td>
<td>Всего лотов: 3 Лот № 1: Земельный участок кадастровый номер
76:12:0000123:456 Лот № 2: Автомобиль BMW
Лот № 3: Квартира в Москве, кадастровый номер 77:01:0001010:1234</td>
</tr></table>
"""


def test_all_twenty_distinct_first_party_etps_have_canonical_url_and_plan():
    assert len(PROFILES) == 22
    assert len(PROFILE_BY_ID) == 22
    for profile in PROFILES:
        assert urlsplit(profile.catalogue_url).scheme == "https"
        assert urlsplit(profile.catalogue_url).hostname
        assert profile.strategy in {"existing", "table_lots", "trade_rows", "links", "research"}
        assert safe_url(profile, profile.catalogue_url) == profile.catalogue_url


@pytest.mark.parametrize("source", [
    "etpu.ru", "centerr.ru", "utpl.ru", "zakazrf.ru",
])
def test_repeated_public_table_pattern_keeps_trade_lot_and_numeric_price(source):
    rows = parse_catalogue(PROFILE_BY_ID[source], PER_LOT)
    assert len(rows) == 1
    row = rows[0]
    assert row["external_id"] == "0000456:1"
    assert row["listing_price_rub"] == 2500000.25
    assert row["cadastral_number"] == "50:05:0010203:1234"
    assert row["verified_detail"] is False
    assert row["ready_for_ingestion"] is False


def test_alfalot_lot_card_link_is_first_party_and_identity_is_trade_plus_lot():
    html = """
    <table><tr><td>0026026018DS.lot2</td>
    <td><a href="/app/LotCard/page?LotCard.lotEntity=LT%3Ademo123">
    Земельный участок 54:20:030601:2341 – 800 кв.м.</a></td>
    <td>290 000.00 руб. начальная цена</td></tr></table>
    """
    rows = parse_catalogue(PROFILE_BY_ID["alfalot.ru"], html)
    assert len(rows) == 1
    assert rows[0]["external_id"] == "0026026018DS:2"
    assert rows[0]["cadastral_number"] == "54:20:030601:2341"
    assert rows[0]["catalogue_detail_link"].startswith(
        "https://etp.alfalot.ru/app/LotCard/page?"
    )
    assert rows[0]["listing_price_rub"] is None
    assert rows[0]["verified_detail"] is False


def test_trade_table_is_split_by_lot_and_not_registered_as_auction_asset():
    p = PROFILE_BY_ID["nistp.ru"]
    rows = parse_catalogue(p, MULTI)
    assert [x["external_id"] for x in rows] == [
        "71874-ОАОФ:1", "71874-ОАОФ:3",
    ]
    assert all(x["listing_price_rub"] is None for x in rows)
    assert all(x["ready_for_ingestion"] is False for x in rows)
    assert all(x["catalogue_detail_link"].startswith("https://nistp.ru/") for x in rows)


@pytest.mark.parametrize("bad", [
    "https://example.com/public/auctions/view/222/",
    "http://bankrupt.utpl.ru/public/auctions/view/222/",
    "//evil.example/path",
    "https://user:secret@bankrupt.utpl.ru/public/lots",
    "https://bankrupt.utpl.ru.evil.example/lots",
    "https://bankrupt.utpl.ru:8443/public/lots",
])
def test_cross_host_or_downgraded_links_are_rejected(bad):
    with pytest.raises(EtpCatalogError):
        safe_url(PROFILE_BY_ID["utpl.ru"], bad)


class StubResponse:
    status_code = 200
    headers = {"Content-Type": "text/html; charset=utf-8"}

    def __init__(self, text):
        self.text = text
        self.content = text.encode("utf-8")

    def raise_for_status(self):
        pass


class StubSession:
    def __init__(self):
        self.urls = []

    def get(self, url, *, timeout, headers, allow_redirects):
        assert allow_redirects is False
        self.urls.append(url)
        if url == PROFILE_BY_ID["etpu.ru"].catalogue_url:
            return StubResponse(PER_LOT)
        if url == PROFILE_BY_ID["nistp.ru"].catalogue_url:
            return StubResponse(MULTI)
        raise AssertionError(f"unexpected host {url}")


def test_joint_probe_never_changes_database_or_proves_full_inventory(monkeypatch):
    import bankrotai.connectors.registry.direct_etp_catalogue as etps

    monkeypatch.setattr(etps.time, "sleep", lambda _: None)
    session = StubSession()
    report = batch_probe(
        session=session, ids=("m-ets.ru", "etpu.ru", "nistp.ru", "ausib.ru"),
        delay_seconds=0.5,
    )
    assert report["expected_source_count"] == 22
    assert report["queried_source_count"] == 4
    assert report["source_completeness_proven"] is False
    assert report["can_reconcile_missing_or_archive"] is False
    assert report["can_publish_to_map"] is False
    assert report["database_mutations"] == 0
    assert [x["status"] for x in report["sources"]] == [
        "existing_connector",
        "candidates_unverified",
        "candidates_unverified",
        "needs_public_catalogue_contract",
    ]
    assert session.urls == [
        PROFILE_BY_ID["etpu.ru"].catalogue_url,
        PROFILE_BY_ID["nistp.ru"].catalogue_url,
    ]


@pytest.mark.parametrize("source", ["rts-tender.ru", "roseltorg.ru"])
def test_property_sale_sections_require_verified_public_listing_contract(source):
    profile = PROFILE_BY_ID[source]
    assert profile.strategy == "research"
    assert "https://" in profile.catalogue_url
    with pytest.raises(EtpCatalogError):
        parse_catalogue(profile, PER_LOT)


def test_research_profile_does_not_fabricate_available_lots():
    profile = PROFILE_BY_ID["etp-profit.ru"]
    with pytest.raises(EtpCatalogError):
        parse_catalogue(profile, PER_LOT)


def test_duplicate_or_invalid_source_rejected_before_network():
    session = StubSession()
    with pytest.raises(ValueError):
        batch_probe(session=session, ids=("etpu.ru", "etpu.ru"))
    with pytest.raises(ValueError):
        batch_probe(session=session, ids=("unlisted-etp.ru",))
    assert session.urls == []
