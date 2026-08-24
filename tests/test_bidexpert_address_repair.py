from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from bankrotai.db import Base, CanonicalLot, ProcessedLot, SourceLot
from bankrotai.services.bidexpert_address_repair import repair_bidexpert_addresses


def test_bidexpert_address_repair_is_safe_and_idempotent() -> None:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    full = "Саратовская область, г. Новоузенск, ул. Рабочая, д. 3"
    with factory() as session:
        canonical = CanonicalLot(canonical_key="source:bidexpert:1", title="Лот", category="realty", address="Саратовская область, г")
        processed = ProcessedLot(external_id="bidexpert:1", source="bidexpert", source_system="bidexpert.ru", title="Лот", description="", category="realty", address="Саратовская область, г")
        session.add_all((canonical, processed))
        session.flush()
        session.add(SourceLot(canonical_lot_id=canonical.id, processed_lot_id=processed.id, source_system="bidexpert.ru", external_id="bidexpert:1", title=f"Лот по адресу: {full}.", address="Саратовская область, г"))
        session.commit()

        assert repair_bidexpert_addresses(session) == {"selected": 1, "repairable": 1, "updated": 0}
        assert session.scalar(select(SourceLot.address)) == "Саратовская область, г"
        assert repair_bidexpert_addresses(session, apply=True) == {"selected": 1, "repairable": 1, "updated": 1}
        session.commit()
        assert session.scalar(select(SourceLot.address)) == full
        repaired = session.scalar(select(ProcessedLot))
        assert repaired is not None and repaired.address == full and repaired.needs_geo_check is True
        assert session.scalar(select(CanonicalLot.address)) == full
        assert repair_bidexpert_addresses(session, apply=True) == {"selected": 1, "repairable": 0, "updated": 0}
