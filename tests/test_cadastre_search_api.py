from __future__ import annotations

import asyncio
from datetime import timedelta

from bankrotai import api
from bankrotai.db import CadastreObjectCache, utc_now
from bankrotai.geo import CadastralObjectResult


def _disable_cache_writes(monkeypatch) -> None:
    monkeypatch.setattr(api, "upsert_cadastre_cache", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(api, "cache_results", lambda *_args, **_kwargs: None)


def test_cadastral_number_search_returns_normalized_object_without_auction_lookup(monkeypatch) -> None:
    geometry = {
        "type": "Polygon",
        "coordinates": [[[39.77, 57.69], [39.78, 57.69], [39.78, 57.70], [39.77, 57.69]]],
    }
    monkeypatch.setattr(api, "get_cached_cadastre_object", lambda *_args, **_kwargs: None)
    _disable_cache_writes(monkeypatch)
    monkeypatch.setattr(
        api._CADASTRAL_GEOCODER,
        "search_by_cadastral_number",
        lambda query: CadastralObjectResult(
            query=query,
            cadastral_number=query,
            object_type="Здание",
            address="г Ярославль, Ленинградский проспект, 54а",
            lat=57.69072,
            lon=39.77901,
            geometry_json=geometry,
            has_boundary=True,
            source="nspd",
            confidence="high",
            info={
                "Вид объекта недвижимости": "Здание",
                "Кадастровый номер": query,
                "Адрес": "г Ярославль, Ленинградский проспект, 54а",
            },
        ),
    )

    result = asyncio.run(api.search_cadastre("76:23:011401:8268", False, None, None))

    assert result["kind"] == "object"
    assert result["object"]["cadastral_number"] == "76:23:011401:8268"
    assert result["object"]["geometry"] == geometry
    assert "geometry_json" not in result["object"]
    assert "raw" not in result["object"]


def test_cadastral_number_search_uses_fresh_persistent_cache_first(monkeypatch) -> None:
    now = utc_now()
    row = CadastreObjectCache(
        cadastral_number="76:23:011401:8268",
        address="Ярославль, Ленинградский проспект, 54а",
        address_normalized="ярославль, ленинградский проспект, 54а",
        object_type="Здание",
        title=None,
        attributes_json={"Кадастровый номер": "76:23:011401:8268"},
        geometry_json={"type": "Point", "coordinates": [39.77901, 57.69072]},
        centroid_lat=57.69072,
        centroid_lon=39.77901,
        source="nspd",
        is_complete=True,
        fetched_at=now,
        expires_at=now + timedelta(days=7),
        created_at=now,
        updated_at=now,
    )
    monkeypatch.setattr(api, "get_cached_cadastre_object", lambda *_args, **_kwargs: row)
    monkeypatch.setattr(
        api._CADASTRAL_GEOCODER,
        "search_by_cadastral_number",
        lambda _query: (_ for _ in ()).throw(AssertionError("provider must not run on fresh cache hit")),
    )

    result = asyncio.run(api.search_cadastre("76:23:011401:8268", False, None, None))

    assert result["kind"] == "object"
    assert result["object"]["source"] == "cache:nspd"
    assert result["object"]["address"] == "Ярославль, Ленинградский проспект, 54а"


def test_address_search_first_returns_choices(monkeypatch) -> None:
    monkeypatch.setattr(
        api.PHOTON_GEOCODER,
        "suggest_addresses",
        lambda query, limit=10: [
            {"label": "Ярославль, Ленинградский проспект, д 105", "lat": 57.691848, "lon": 39.771867},
            {"label": "Нижний Тагил, Ленинградский проспект, д 105", "lat": 57.9, "lon": 59.9},
        ],
    )
    monkeypatch.setattr(
        api._CADASTRAL_GEOCODER,
        "search_objects_by_point",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("WMS must not run before address selection")
        ),
    )

    result = asyncio.run(api.search_cadastre("Ленинградский 105", False, None, None))

    assert result["kind"] == "address_suggestions"
    assert [item["label"] for item in result["items"]] == [
        "Ярославль, Ленинградский проспект, д 105",
        "Нижний Тагил, Ленинградский проспект, д 105",
    ]


def test_selected_address_returns_cadastral_object_choices_from_nspd_wms(monkeypatch) -> None:
    monkeypatch.setattr(api, "find_cached_cadastre_candidates", lambda *_args, **_kwargs: [])
    _disable_cache_writes(monkeypatch)
    monkeypatch.setattr(
        api._CADASTRAL_GEOCODER,
        "search_objects_by_point",
        lambda lat, lon, fallback_address=None: [
            CadastralObjectResult(
                query=fallback_address or "",
                cadastral_number="76:23:010101:15008",
                object_type="Здание",
                address=fallback_address,
                lat=lat,
                lon=lon,
                source="nspd_wms",
                confidence="high",
                info={
                    "Вид объекта недвижимости": "Здание",
                    "Кадастровый номер": "76:23:010101:15008",
                    "Адрес": fallback_address,
                },
            ),
            CadastralObjectResult(
                query=fallback_address or "",
                cadastral_number="76:23:010101:123",
                object_type="Земельный участок",
                address=fallback_address,
                lat=lat,
                lon=lon,
                source="nspd_wms",
                confidence="high",
                info={
                    "Вид объекта недвижимости": "Земельный участок",
                    "Кадастровый номер": "76:23:010101:123",
                    "Адрес": fallback_address,
                },
            ),
        ],
    )

    result = asyncio.run(
        api.search_cadastre(
            "Ярославль, Ленинградский проспект, д 105",
            True,
            57.691848,
            39.771867,
        )
    )

    assert result["kind"] == "cadastral_objects"
    assert [item["cadastral_number"] for item in result["items"]] == [
        "76:23:010101:15008",
        "76:23:010101:123",
    ]
    assert result["lat"] == 57.691848
    assert result["lon"] == 39.771867


def test_selected_address_uses_cached_candidates_before_wms(monkeypatch) -> None:
    now = utc_now()
    cached = CadastreObjectCache(
        cadastral_number="76:23:010101:15008",
        address="Ярославль, Ленинградский проспект, д 105",
        address_normalized="ярославль, ленинградский проспект, д 105",
        object_type="Здание",
        title=None,
        attributes_json={"Кадастровый номер": "76:23:010101:15008"},
        geometry_json=None,
        centroid_lat=57.691848,
        centroid_lon=39.771867,
        source="nspd_wms",
        is_complete=False,
        fetched_at=now,
        expires_at=now + timedelta(days=7),
        created_at=now,
        updated_at=now,
    )
    monkeypatch.setattr(api, "find_cached_cadastre_candidates", lambda *_args, **_kwargs: [cached])
    monkeypatch.setattr(
        api._CADASTRAL_GEOCODER,
        "search_objects_by_point",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("WMS must not run on fresh cache hit")),
    )

    result = asyncio.run(
        api.search_cadastre(
            "Ярославль, Ленинградский проспект, д 105",
            True,
            57.691848,
            39.771867,
        )
    )

    assert result["kind"] == "cadastral_objects"
    assert result["items"][0]["source"] == "cache:nspd_wms"
    assert result["items"][0]["cadastral_number"] == "76:23:010101:15008"
