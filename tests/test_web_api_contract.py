from decimal import Decimal

import pytest

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from bankrotai.db import Base, ProcessedLot
from bankrotai.logic import build_lots_response, get_lot_response


def _session() -> Session:
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return Session(engine)


def test_yaroslavl_alias_reads_region_code_76_lots() -> None:
    session = _session()
    lot = ProcessedLot(
        external_id="lot-1",
        source="test",
        source_system="test",
        title="Земельный участок",
        description="Тестовый лот",
        category="land",
        region_slug="76",
        current_price=Decimal("1000000.00"),
        auction_status="active",
        risk_score=4,
        rating=71.5,
    )
    session.add(lot)
    session.commit()

    response = build_lots_response(session, "yaroslavl")

    assert response["total"] == 1
    assert response["items"][0]["region_slug"] == "76"


def test_lot_detail_response_is_populated() -> None:
    session = _session()
    lot = ProcessedLot(
        external_id="lot-2",
        source="test",
        source_system="test",
        title="Нежилое помещение",
        description="Подробное описание",
        category="commercial_room",
        region_slug="76",
        current_price=Decimal("2500000.00"),
        auction_status="scheduled",
        risk_score=3,
        rating=82.0,
        cadastral_number="76:23:010101:10",
    )
    session.add(lot)
    session.commit()

    response = get_lot_response(session, "yaroslavl", lot.id)

    assert response is not None
    assert response["title"] == "Нежилое помещение"
    assert response["cadastral_number"] == "76:23:010101:10"


def _searchable_lot() -> ProcessedLot:
    return ProcessedLot(
        external_id="EXT-Search-42",
        source="test",
        source_system="test",
        title="Складской комплекс",
        description="Производственная площадка у реки",
        object_name="Логистический центр Север",
        category="commercial_building",
        region_slug="76",
        address="Ярославль, Промышленная улица, 7",
        cadastral_number="76:23:010101:987",
        cadastral_numbers=["76:23:010101:987", "76:23:010101:988"],
        current_price=Decimal("5000000.00"),
        auction_status="active",
        risk_score=None,
    )


def test_default_listing_includes_unrated_lots() -> None:
    session = _session()
    session.add(_searchable_lot())
    session.commit()
    response = build_lots_response(session, "yaroslavl")
    assert response["total"] == 1
    assert response["items"][0]["risk_score"] is None


def test_explicit_risk_filter_excludes_unrated_lots() -> None:
    session = _session()
    session.add(_searchable_lot())
    session.commit()
    assert build_lots_response(session, "yaroslavl", min_risk=1, max_risk=5)["total"] == 0


@pytest.mark.parametrize(
    "term",
    [
        "СКЛАДСКОЙ",
        "площадка у реки",
        "промышленная улица",
        "010101:98",
        "EXT-search-42",
        "центр север",
    ],
)
def test_search_covers_all_documented_fields_case_insensitively(term: str) -> None:
    session = _session()
    session.add(_searchable_lot())
    session.commit()
    assert build_lots_response(session, "yaroslavl", search=f"  {term}  ")["total"] == 1


def test_empty_search_does_not_change_listing() -> None:
    session = _session()
    session.add(_searchable_lot())
    session.commit()
    assert build_lots_response(session, "yaroslavl", search="   ")["total"] == 1
