from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from bankrotai.db import Base, CanonicalLot, GeoFailure, ProcessedLot, SourceLot
from bankrotai.domain import NormalizedLot
from bankrotai.services.source_detail_geo_repair import repair_source_details_for_geo


def _session() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def _seed(session: Session) -> int:
    processed = ProcessedLot(
        external_id="torgi_gov:21000004710000027207_1",
        source="torgi_gov",
        source_system="torgi.gov.ru",
        title="Продажа нежилого помещения (машино-место)",
        description="Нежилое помещение в г.о. Подольск",
        category="real_estate",
        region_code="50",
        region_name="Московская область",
        auction_status="active",
        address=None,
        cadastral_number=None,
    )
    session.add(processed)
    session.flush()
    canonical = CanonicalLot(
        canonical_key="torgi-gov:test:1",
        title=processed.title,
        category="real_estate",
        region_code="50",
    )
    session.add(canonical)
    session.flush()
    session.add(SourceLot(
        canonical_lot_id=canonical.id,
        processed_lot_id=processed.id,
        source_system="torgi.gov.ru",
        external_id=processed.external_id,
        title=processed.title,
        description=processed.description,
        region_code="50",
    ))
    session.add(GeoFailure(
        lot_id=processed.id,
        status="deferred_no_match",
        attempt_count=5,
        error_message="No validated coordinates",
    ))
    session.commit()
    return int(processed.id)


def _detail() -> NormalizedLot:
    return NormalizedLot(
        external_id="torgi_gov:21000004710000027207_1",
        source="torgi_gov",
        source_system="torgi.gov.ru",
        title="Продажа нежилого помещения (машино-место)",
        description="Нежилое помещение",
        category="real_estate",
        region_slug="moskovskaya-oblast",
        region_name="Московская область",
        address="обл. Московская, г.о. Подольск, г. Подольск, ул. Парковая, дом 3 соор. 1",
        cadastral_number="50:55:0030416:1229",
        vin=None,
        area=None,
        start_price=None,
        current_price=None,
        auction_status="active",
        lot_url="https://torgi.gov.ru/new/public/lots/lot/21000004710000027207_1/(lotInfo:info)?fromRec=false",
        source_url="https://torgi.gov.ru/new/public/lots/lot/21000004710000027207_1/(lotInfo:info)?fromRec=false",
        detail_level="detail",
        raw_data={"source": "torgi_gov_detail_json"},
    )


def test_source_detail_geo_repair_dry_run_keeps_db_unchanged() -> None:
    session = _session()
    lot_id = _seed(session)

    result = repair_source_details_for_geo(
        session, details=[(lot_id, _detail())], apply=False
    )

    assert result["eligible"] == 1
    assert result["updated"] == 0
    row = session.get(ProcessedLot, lot_id)
    assert row.address is None
    assert row.cadastral_number is None
    session.close()


def test_source_detail_geo_repair_fills_only_geo_inputs_and_requeues() -> None:
    session = _session()
    lot_id = _seed(session)

    result = repair_source_details_for_geo(
        session, details=[(lot_id, _detail())], apply=True
    )
    session.commit()

    row = session.get(ProcessedLot, lot_id)
    source = session.query(SourceLot).filter_by(processed_lot_id=lot_id).one()
    canonical = session.get(CanonicalLot, source.canonical_lot_id)
    failure = session.query(GeoFailure).filter_by(lot_id=lot_id).one()
    assert row.address == _detail().address
    assert row.cadastral_number == "50:55:0030416:1229"
    assert row.cadastral_numbers == ["50:55:0030416:1229"]
    assert row.needs_geo_check is True
    assert source.address == row.address
    assert source.cadastral_number == row.cadastral_number
    assert canonical.address == row.address
    assert canonical.cadastral_number == row.cadastral_number
    assert failure.status == "queued"
    assert failure.attempt_count == 0
    assert result["updated"] == 1
    assert result["addresses_filled"] == 1
    assert result["cadastres_filled"] == 1
    session.close()


def test_source_detail_geo_repair_rejects_wrong_exact_identity() -> None:
    session = _session()
    lot_id = _seed(session)
    detail = _detail()
    detail.external_id = "torgi_gov:21000004710000099999_1"

    result = repair_source_details_for_geo(
        session, details=[(lot_id, detail)], apply=True
    )

    assert result["eligible"] == 0
    assert result["updated"] == 0
    assert result["rejections"] == [{"lot_id": lot_id, "reason": "external_id_mismatch"}]
    assert session.get(ProcessedLot, lot_id).address is None
    session.close()


def test_source_detail_geo_repair_rejects_cadastral_region_mismatch() -> None:
    session = _session()
    lot_id = _seed(session)
    detail = _detail()
    detail.cadastral_number = "77:01:0000000:1"

    result = repair_source_details_for_geo(
        session, details=[(lot_id, detail)], apply=True
    )

    assert result["eligible"] == 0
    assert result["rejections"] == [{"lot_id": lot_id, "reason": "cadastral_region_mismatch"}]
    session.close()
