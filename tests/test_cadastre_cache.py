from __future__ import annotations

from datetime import timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from bankrotai.db import Base, CadastreObjectCache, utc_now
from bankrotai.geo import CadastralObjectResult
from bankrotai.services.cadastre_cache import (
    cached_row_to_result,
    find_cached_cadastre_candidates,
    get_cached_cadastre_object,
    normalize_cadastre_address,
    upsert_cadastre_cache,
)


def _session() -> Session:
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return Session(engine)


def test_persistent_cadastre_cache_round_trip() -> None:
    session = _session()
    result = CadastralObjectResult(
        query="76:23:011401:8268",
        cadastral_number="76:23:011401:8268",
        object_type="Здание",
        address="Ярославль, Ленинградский проспект, 54а",
        lat=57.69072,
        lon=39.77901,
        geometry_json={"type": "Polygon", "coordinates": [[[39.77, 57.69], [39.78, 57.69], [39.77, 57.69]]]},
        has_boundary=True,
        source="nspd",
        confidence="high",
        info={"Кадастровый номер": "76:23:011401:8268", "Адрес": "Ярославль, Ленинградский проспект, 54а"},
    )

    row = upsert_cadastre_cache(session, result, complete=True)
    session.commit()

    assert row is not None
    cached = get_cached_cadastre_object(
        session,
        "76:23:011401:8268",
        require_complete=True,
        fresh_only=True,
    )
    assert cached is not None
    restored = cached_row_to_result(cached, source_prefix="cache")
    assert restored.cadastral_number == "76:23:011401:8268"
    assert restored.address == "Ярославль, Ленинградский проспект, 54а"
    assert restored.geometry_json == result.geometry_json
    assert restored.source == "cache:nspd"


def test_wms_preview_does_not_downgrade_complete_cache() -> None:
    session = _session()
    complete = CadastralObjectResult(
        query="76:23:010101:15008",
        cadastral_number="76:23:010101:15008",
        object_type="Здание",
        address="Официальный адрес",
        lat=57.6918,
        lon=39.7718,
        source="nspd",
        confidence="high",
        info={"Кадастровый номер": "76:23:010101:15008", "Адрес": "Официальный адрес"},
    )
    upsert_cadastre_cache(session, complete, complete=True)

    preview = CadastralObjectResult(
        query="Ленинградский 105",
        cadastral_number="76:23:010101:15008",
        object_type="Здание",
        address="Photon адрес",
        lat=57.6919,
        lon=39.7720,
        source="nspd_wms",
        confidence="high",
        info={"Кадастровый номер": "76:23:010101:15008"},
    )
    upsert_cadastre_cache(session, preview, complete=False)
    session.commit()

    row = session.get(CadastreObjectCache, "76:23:010101:15008")
    assert row is not None
    assert row.is_complete is True
    assert row.source == "nspd"
    assert row.address == "Официальный адрес"


def test_address_candidate_cache_uses_address_or_nearby_point() -> None:
    session = _session()
    now = utc_now()
    session.add(
        CadastreObjectCache(
            cadastral_number="76:23:010101:15008",
            address="Ярославль, Ленинградский проспект, д 105",
            address_normalized=normalize_cadastre_address("Ярославль, Ленинградский проспект, д 105"),
            object_type="Здание",
            title=None,
            attributes_json={"Кадастровый номер": "76:23:010101:15008"},
            geometry_json=None,
            centroid_lat=57.6919,
            centroid_lon=39.7720,
            source="nspd_wms",
            is_complete=False,
            fetched_at=now,
            expires_at=now + timedelta(days=7),
            created_at=now,
            updated_at=now,
        )
    )
    session.commit()

    rows = find_cached_cadastre_candidates(
        session,
        address="Ярославль, Ленинградский проспект, д 105",
        lat=57.69193,
        lon=39.77201,
    )

    assert [row.cadastral_number for row in rows] == ["76:23:010101:15008"]
