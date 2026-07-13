from decimal import Decimal

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
