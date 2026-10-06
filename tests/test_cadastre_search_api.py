from __future__ import annotations

import asyncio

from bankrotai import api
from bankrotai.geo import CadastralObjectResult


def test_cadastral_number_search_returns_normalized_object_without_auction_lookup(monkeypatch) -> None:
    geometry = {
        "type": "Polygon",
        "coordinates": [[[39.77, 57.69], [39.78, 57.69], [39.78, 57.70], [39.77, 57.69]]],
    }

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

    result = asyncio.run(api.search_cadastre("76:23:011401:8268", False))

    assert result["kind"] == "object"
    assert result["object"]["cadastral_number"] == "76:23:011401:8268"
    assert result["object"]["geometry"] == geometry
    assert "geometry_json" not in result["object"]
    assert "raw" not in result["object"]


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
        "search_selected_address",
        lambda _query: (_ for _ in ()).throw(AssertionError("address must not resolve before selection")),
    )

    result = asyncio.run(api.search_cadastre("Ленинградский 105", False))

    assert result["kind"] == "address_suggestions"
    assert [item["label"] for item in result["items"]] == [
        "Ярославль, Ленинградский проспект, д 105",
        "Нижний Тагил, Ленинградский проспект, д 105",
    ]


def test_selected_address_returns_detailed_cadastral_object(monkeypatch) -> None:
    monkeypatch.setattr(
        api._CADASTRAL_GEOCODER,
        "search_selected_address",
        lambda query: CadastralObjectResult(
            query=query,
            cadastral_number="76:23:010101:15008",
            object_type="Здание",
            address="Российская Федерация, Ярославская область, г. Ярославль, пр-кт Ленинградский, д. 105",
            lat=57.691848,
            lon=39.771867,
            has_boundary=False,
            source="nspd",
            confidence="high",
            info={
                "Вид объекта недвижимости": "Здание",
                "Дата присвоения": "01.07.2012",
                "Кадастровый номер": "76:23:010101:15008",
                "Кадастровый квартал": "76:23:011304",
            },
        ),
    )

    result = asyncio.run(
        api.search_cadastre("Ярославль, Ленинградский проспект, д 105", True)
    )

    assert result["kind"] == "object"
    assert result["object"]["cadastral_number"] == "76:23:010101:15008"
    assert result["object"]["has_boundary"] is False
    assert result["object"]["info"]["Дата присвоения"] == "01.07.2012"
