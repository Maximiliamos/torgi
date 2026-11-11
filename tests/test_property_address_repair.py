from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from bankrotai.db import Base, CanonicalLot, GeoFailure, ProcessedLot, SourceLot
from bankrotai.services.property_address_repair import repair_property_addresses


def _session() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def _seed(session: Session) -> int:
    processed = ProcessedLot(
        external_id="sample-1",
        source="test",
        source_system="sistematorg.com",
        title="Земельный участок",
        description=(
            "Земельный участок. Кадастровый номер: 02:53:120401:62. "
            "Почтовый адрес ориентира: Республика Башкортостан, р-н. Шаранский, "
            "с. Сюньбаш, ул. Центральная, д. 16. Площадь: 3288"
        ),
        category="land",
        cadastral_number="02:53:120401:62",
        auction_status="active",
        address=None,
    )
    session.add(processed)
    session.flush()
    canonical = CanonicalLot(
        canonical_key="test:asset:1",
        title="Земельный участок",
        category="land",
        cadastral_number="02:53:120401:62",
    )
    session.add(canonical)
    session.flush()
    session.add(SourceLot(
        canonical_lot_id=canonical.id,
        processed_lot_id=processed.id,
        source_system="sistematorg.com",
        external_id="sistematorg.com:66720",
        title=processed.title,
        description=processed.description,
        cadastral_number=processed.cadastral_number,
        address=None,
    ))
    session.add(GeoFailure(
        lot_id=processed.id,
        status="deferred_no_match",
        attempt_count=4,
        error_message="No validated coordinates",
    ))
    session.commit()
    return int(processed.id)


def test_property_address_repair_dry_run_is_read_only() -> None:
    session = _session()
    lot_id = _seed(session)

    result = repair_property_addresses(session, lot_ids=[lot_id], apply=False)

    assert result["repairable"] == 1
    assert result["updated"] == 0
    assert session.get(ProcessedLot, lot_id).address is None
    failure = session.query(GeoFailure).filter_by(lot_id=lot_id).one()
    assert failure.status == "deferred_no_match"
    session.close()


def test_property_address_repair_updates_linked_rows_and_requeues_geo() -> None:
    session = _session()
    lot_id = _seed(session)

    result = repair_property_addresses(session, lot_ids=[lot_id], apply=True)
    session.commit()

    expected = (
        "Республика Башкортостан, р-н. Шаранский, с. Сюньбаш, "
        "ул. Центральная, д. 16"
    )
    processed = session.get(ProcessedLot, lot_id)
    source = session.query(SourceLot).filter_by(processed_lot_id=lot_id).one()
    canonical = session.get(CanonicalLot, source.canonical_lot_id)
    failure = session.query(GeoFailure).filter_by(lot_id=lot_id).one()
    assert processed.address == expected
    assert processed.needs_geo_check is True
    assert processed.geo_input_hash is None
    assert source.address == expected
    assert canonical.address == expected
    assert failure.status == "queued"
    assert failure.attempt_count == 0
    assert failure.next_retry_at is not None
    assert result["updated"] == 1
    assert result["geo_requeued"] == 1
    session.close()


def test_property_address_repair_never_overwrites_existing_address() -> None:
    session = _session()
    lot_id = _seed(session)
    processed = session.get(ProcessedLot, lot_id)
    processed.address = "Проверенный существующий адрес"
    session.commit()

    result = repair_property_addresses(session, lot_ids=[lot_id], apply=True)

    assert result["repairable"] == 0
    assert result["updated"] == 0
    assert session.get(ProcessedLot, lot_id).address == "Проверенный существующий адрес"
    session.close()
