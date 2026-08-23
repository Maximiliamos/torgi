from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from bankrotai.db import Base, CanonicalLot, ProcessedLot, SourceLot
from bankrotai.services.read_model_repair import repair_missing_processed_links


def test_repair_missing_processed_link_is_idempotent() -> None:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        canonical = CanonicalLot(canonical_key="cadastral:76:23:1:1", title="Участок", category="land")
        session.add(canonical)
        session.flush()
        session.add(SourceLot(
            canonical_lot_id=canonical.id,
            source_system="torgi.gov.ru",
            external_id="gis-1",
            title="Участок",
            description="Ярославль",
            category="land",
            region_code="76",
            region_name="Ярославская область",
            address="Ярославль, ул. Свободы, 1",
            cadastral_number="76:23:1:1",
            start_price=100000,
            source_status="active",
            is_active=True,
            is_archived=False,
            raw_data={"region_code": "76"},
        ))
        session.commit()

        assert repair_missing_processed_links(session) == {"selected": 1, "repaired": 1}
        source = session.scalar(select(SourceLot))
        assert source is not None and source.processed_lot_id is not None
        assert session.scalar(select(func.count()).select_from(ProcessedLot)) == 1
        assert repair_missing_processed_links(session) == {"selected": 0, "repaired": 0}
        assert session.scalar(select(func.count()).select_from(ProcessedLot)) == 1
