from datetime import datetime

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from bankrotai.db import Base, CanonicalLot, LotPriceEvent, ProcessedLot, SourceLot
from bankrotai.services.price_schedule import active_schedule_period, recalculate_public_offer_prices


SCHEDULE = [
    {"starts_at": "2026-09-08T10:00:00", "ends_at": "2026-09-14T10:00:00", "price": 4_500_000},
    {"starts_at": "2026-09-14T10:00:00", "ends_at": "2026-09-21T10:00:00", "price": 4_275_000},
    {"starts_at": "2026-09-21T10:00:00", "ends_at": "2026-09-28T10:00:00", "price": 4_050_000},
]


def test_active_schedule_period_before_on_and_after_boundary() -> None:
    before = active_schedule_period(SCHEDULE, now=datetime(2026, 9, 14, 9, 59, 59))
    boundary = active_schedule_period(SCHEDULE, now=datetime(2026, 9, 14, 10, 0))
    after = active_schedule_period(SCHEDULE, now=datetime(2026, 9, 28, 10, 0))

    assert before and float(before[0]) == 4_500_000
    assert boundary and float(boundary[0]) == 4_275_000
    assert boundary[1] == datetime(2026, 9, 21, 10, 0)
    assert boundary[2] is not None and float(boundary[2]) == 4_050_000
    assert after is None


def test_recalculate_updates_source_read_model_and_history_once() -> None:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        processed = ProcessedLot(
            external_id="tbankrot:7991236", source="tbankrot", source_system="tbankrot.ru",
            title="Лот", description="", category="land", auction_status="active",
            current_price=4_500_000,
        )
        session.add(processed)
        session.flush()
        canonical = CanonicalLot(
            canonical_key="test-price", legacy_processed_lot_id=processed.id,
            title="Лот", category="land",
        )
        session.add(canonical)
        session.flush()
        session.add(SourceLot(
            canonical_lot_id=canonical.id,
            processed_lot_id=processed.id,
            source_system="tbankrot.ru",
            external_id="tbankrot:7991236",
            source_status="active",
            current_price=4_500_000,
            public_offer_schedule=SCHEDULE,
        ))
        session.commit()

    first = recalculate_public_offer_prices(factory, now=datetime(2026, 9, 15, 12, 0))
    second = recalculate_public_offer_prices(factory, now=datetime(2026, 9, 15, 12, 1))

    assert first == {"checked": 1, "changed": 1}
    assert second == {"checked": 1, "changed": 0}
    with factory() as session:
        source = session.scalar(select(SourceLot))
        processed = session.scalar(select(ProcessedLot))
        events = session.scalars(select(LotPriceEvent)).all()
        assert source is not None and float(source.current_price or 0) == 4_275_000
        assert processed is not None and float(processed.current_price or 0) == 4_275_000
        assert len(events) == 1
        assert events[0].metadata_json["reason"] == "public_offer_period_transition"
