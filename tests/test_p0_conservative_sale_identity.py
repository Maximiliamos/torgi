"""P0: physical-object coincidence is never evidence of one auction sale.

These tests are offline: no live ETP and no production PostgreSQL writes.
"""
from types import SimpleNamespace

from bankrotai.logic import (
    _canonical_key,
    _same_cross_source_lot,
    _same_processed_cross_source_lot,
    _same_published_sale_url,
)


def item(source, external, url, cadastral="76:02:022201:38"):
    return SimpleNamespace(
        source_system=source,
        external_id=external,
        lot_url=url,
        source_url=url,
        cadastral_number=cadastral,
        cadastral_numbers=[cadastral] if cadastral else [],
        title="Земельный участок 1000 м²",
        address="Один адрес",
        region_slug="yaroslavl",
        current_price=1_000_000,
        start_price=1_000_000,
    )


def test_two_sales_with_one_cadastral_and_same_price_do_not_merge():
    first = item("m-ets.ru", "lot-101", "https://m-ets.ru/auction/lot-101")
    second = item("cdtrf.ru", "lot-202", "https://cdtrf.ru/auction/lot-202")
    assert _same_cross_source_lot(first, second) is False
    assert _same_processed_cross_source_lot(first, second) is False
    assert _canonical_key(first) != _canonical_key(second)


def test_matching_publication_and_cadastral_may_be_same_sale():
    shared = "https://etp.example/auction/real-estate/lot-1234"
    first = item("aggregator.ru", "src-10", shared)
    second = item("etp.example", "direct-10", shared)
    assert _same_published_sale_url(first, second)
    assert _same_cross_source_lot(first, second)
    assert _same_processed_cross_source_lot(first, second)


def test_same_publication_url_for_different_cadastral_not_merge():
    shared = "https://etp.example/auction/real-estate/lot-1234"
    first = item("aggregator.ru", "a", shared)
    second = item("etp.example", "b", shared, "77:01:0004010:119")
    assert not _same_cross_source_lot(first, second)
    assert not _same_processed_cross_source_lot(first, second)


def test_similar_title_address_price_not_strong_sale_identity():
    first = item("m-ets.ru", "a", "https://m-ets.ru/auction/one", None)
    second = item("cdtrf.ru", "b", "https://cdtrf.ru/auction/two", None)
    assert not _same_cross_source_lot(first, second)


def test_same_source_never_marked_cross_source_duplicate():
    shared = "https://etp.example/auction/real-estate/lot-1234"
    first = item("etp.example", "a", shared)
    second = item("etp.example", "b", shared)
    assert not _same_cross_source_lot(first, second)
    assert not _same_processed_cross_source_lot(first, second)


def test_generic_catalogue_url_is_not_sale_evidence():
    first = item("m-ets.ru", "a", "https://m-ets.ru/")
    second = item("cdtrf.ru", "b", "https://m-ets.ru/")
    assert not _same_cross_source_lot(first, second)


def test_idempotent_source_key_even_when_cadastre_changes():
    first = item("m-ets.ru", "a", "https://m-ets.ru/auction/one")
    second = item("m-ets.ru", "a", "https://m-ets.ru/auction/one", "77:01:0004010:119")
    assert _canonical_key(first) == _canonical_key(second)


def test_persist_new_different_auctions_with_same_property_stays_separate():
    from sqlalchemy import create_engine, func, select
    from sqlalchemy.orm import Session

    from bankrotai.db import Base, CanonicalLot, ProcessedLot, SourceLot
    from bankrotai.domain import NormalizedLot
    from bankrotai.logic import persist_lot

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    def normalized(source, external, url):
        return NormalizedLot(
            source=source,
            source_system=source,
            external_id=external,
            title="Земельный участок",
            description="Продажа",
            category="land",
            region_slug="yaroslavl",
            region_name="Ярославская область",
            address="Одинаковый адрес",
            cadastral_number="76:02:022201:38",
            vin=None,
            area=1000,
            start_price=1000000,
            current_price=1000000,
            auction_status="active",
            lot_url=url,
            source_url=url,
            detail_level="detail",
            raw_data={},
        )

    with Session(engine) as session:
        left = normalized("m-ets.ru", "sale-A", "https://m-ets.ru/auction/sale-A")
        right = normalized("cdtrf.ru", "sale-B", "https://cdtrf.ru/auction/sale-B")
        a = persist_lot(session, left)
        b = persist_lot(session, right)
        session.flush()
        assert a.id != b.id
        assert b.duplicate_of_id is None
        assert session.scalar(select(func.count()).select_from(CanonicalLot)) == 2
        assert session.scalar(select(func.count()).select_from(SourceLot)) == 2
        assert session.scalar(select(func.count()).select_from(ProcessedLot)) == 2
        assert persist_lot(session, left).id == a.id
        session.flush()
        assert session.scalar(select(func.count()).select_from(ProcessedLot)) == 2
