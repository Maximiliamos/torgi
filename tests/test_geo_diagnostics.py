from __future__ import annotations

import json

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from bankrotai.db import Base, GeoFailure, LotGeoSnapshot, ProcessedLot
from bankrotai.services.geo_backfill import geocoding_diagnostic_report


def _lot(
    external_id: str,
    *,
    region_code: str,
    cadastral_number: str | None,
    address: str | None,
) -> ProcessedLot:
    return ProcessedLot(
        external_id=external_id,
        source="test",
        source_system="test",
        title=external_id,
        description="",
        category="land",
        region_code=region_code,
        region_name="Ярославская область" if region_code == "76" else "Московская область",
        cadastral_number=cadastral_number,
        address=address,
        auction_status="active",
    )


def test_geocoding_diagnostic_report_aggregates_quality_without_raw_addresses() -> None:
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        mapped = _lot(
            "mapped",
            region_code="76",
            cadastral_number="76:23:010101:1",
            address="Ярославль, улица Свободы, 1",
        )
        failed = _lot(
            "failed",
            region_code="76",
            cadastral_number="76:23:010101:2",
            address="Ярославль, неизвестный адрес",
        )
        mismatch = _lot(
            "mismatch",
            region_code="50",
            cadastral_number="76:23:010101:3",
            address="Ярославль, улица Победы, 1",
        )
        session.add_all([mapped, failed, mismatch])
        session.flush()
        session.add(
            LotGeoSnapshot(
                lot_id=mapped.id,
                geo_source="photon",
                geo_method="address",
                geo_confidence="high",
                centroid_lat=57.6261,
                centroid_lon=39.8845,
                metadata_json={"address": "Ярославль, улица Свободы, 1"},
            )
        )
        session.add(
            GeoFailure(
                lot_id=failed.id,
                status="queued",
                attempt_count=3,
                error_message=json.dumps(
                    {
                        "error": "No validated coordinates",
                        "attempts": [
                            {"source": "photon", "reason": "locality_mismatch"},
                        ],
                    },
                    ensure_ascii=False,
                ),
            )
        )
        session.commit()

        report = geocoding_diagnostic_report(session)

    assert report["progress"]["total"] == 3
    assert report["progress"]["geocoded"] == 1
    assert report["cfo"]["76"] == {
        "eligible": 2,
        "mapped": 1,
        "unmapped": 1,
        "percent": 50.0,
    }
    assert report["failures"]["total_open"] == 1
    assert report["failures"]["by_status"] == {"queued": 1}
    assert report["failures"]["by_attempt_count"] == {"3": 1}
    assert report["failures"]["top_reasons"] == {"photon:locality_mismatch": 1}
    assert report["quality"]["cadastral_region_comparable"] == 3
    assert report["quality"]["cadastral_region_mismatch"] == 1
    assert report["quality"]["top_cadastral_region_mismatches"] == {"50->76": 1}
    serialized = json.dumps(report, ensure_ascii=False)
    assert "улица Свободы" not in serialized
    assert "неизвестный адрес" not in serialized
