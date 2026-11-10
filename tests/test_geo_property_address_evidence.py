"""Broad offline regressions for property-address evidence, not five ID fixes.

All fixtures are synthetic/reduced address patterns without personal data.
Providers are mocked; no network calls or production DB writes.
"""
from __future__ import annotations

import pytest

import bankrotai.geo as geo
from bankrotai.geo import CadastralObjectResult, build_geocoding_address_candidates
from bankrotai.services.property_address_evidence import (
    extract_asset_property_address,
    prefer_evidenced_property_address,
)


@pytest.mark.parametrize(
    ("text", "cadastre", "expected"),
    [
        (
            "Земельный участок, 1500 кв.м., номер 47:22:0325001:373, "
            "расположенный по адресу: Ленинградская область, "
            "Волосовский район, д Прологи. В залоге у банка.",
            "47:22:0325001:373",
            "Ленинградская область, Волосовский район, д Прологи",
        ),
        (
            "Квартира по адресу: г. Москва, пр-кт Ленинградский, "
            "д. 52, кв. 108, к/н: 77:09:0004009:5974",
            "77:09:0004009:5974",
            "г. Москва, пр-кт Ленинградский, д. 52, кв. 108",
        ),
        (
            "Квартира, адрес (местонахождение): Россия, Костромская область, "
            "р-н Межевской, с Георгиевское, ул Октябрьская, д 46, кв 4, "
            "кадастровый (условный) номер: 44:11:080105:59",
            "44:11:080105:59",
            "Россия, Костромская область, р-н Межевской, "
            "с Георгиевское, ул Октябрьская, д 46, кв 4",
        ),
        (
            "Квартира, кадастровый номер: 77:09:0006011:3900, "
            "адрес: город Москва, вн.тер.г. муниципальный округ "
            "Молжаниновский, шоссе Ленинградское, дом 228, "
            "корпус 4, квартира 363",
            "77:09:0006011:3900",
            "город Москва, вн.тер.г. муниципальный округ "
            "Молжаниновский, шоссе Ленинградское, дом 228, "
            "корпус 4, квартира 363",
        ),
    ],
)
def test_asset_scoped_address_patterns(text, cadastre, expected):
    assert extract_asset_property_address(
        description=text, cadastral_number=cadastre,
    ) == expected


def test_source_title_beats_unrelated_postal_seller_address():
    title = (
        "Квартира по адресу: г. Москва, пр-кт Ленинградский, "
        "д. 52, кв. 108, к/н: 77:09:0004009:5974"
    )
    other_party = "Г.МОСКВА, УЛ САДОВНИЧЕСКАЯ, Д. 72, СТР. 1"
    chosen = prefer_evidenced_property_address(
        other_party, title=title, cadastral_number="77:09:0004009:5974",
    )
    assert "Ленинградский" in chosen
    assert "САДОВНИЧЕСКАЯ" not in chosen.upper()
    candidates = build_geocoding_address_candidates(
        other_party, title=title, region_name="г. Москва",
    )
    assert "Ленинградский" in candidates[0]
    assert "САДОВНИЧЕСКАЯ" not in candidates[0].upper()


def test_conflicting_full_asset_addresses_are_not_silently_resolved():
    title = "Квартира, адрес: г. Москва, улица Лесная, дом 5"
    description = "Квартира, адрес: г. Москва, улица Цветочная, дом 9"
    assert extract_asset_property_address(title=title, description=description) is None
    stored = "г. Москва, улица Сосновая, дом 11"
    assert prefer_evidenced_property_address(
        stored, title=title, description=description,
    ) == stored


def test_cadastral_region_disagreement_rejects_asset_text():
    text = "Адрес: Москва, Ленинградское шоссе, дом 228, квартира 363"
    assert extract_asset_property_address(
        description=text, cadastral_number="47:22:0325001:373",
    ) is None


def test_absent_asset_evidence_does_not_replace_authoritative_input():
    assert prefer_evidenced_property_address(
        "г. Ярославль, ул. Свободы, д. 5",
        description="Адрес продавца: г. Москва, ул. Тверская, д. 1",
        cadastral_number="76:23:010101:42",
    ) == "г. Ярославль, ул. Свободы, д. 5"


def test_cadastre_supplies_region_to_resolver_with_mocked_providers(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(
        geo.CADASTRAL_GEOCODER, "_search_nspd_geoportal",
        lambda *_args, **_kwargs: None,
    )

    def local_geocoder(address: str, *, allow_nominatim: bool = True):
        calls.append(address)
        if "Ленинградская область" not in address:
            raise AssertionError("Cadastral region was not supplied")
        return CadastralObjectResult(
            query=address,
            address="Прологи, Ленинградская область",
            lat=59.0, lon=29.0,
            source="photon",
            confidence="high",
        )

    monkeypatch.setattr(geo.CADASTRAL_GEOCODER, "search_by_address", local_geocoder)
    result = geo.resolve_lot_geo(
        cadastral_number="47:22:0325001:373",
        title="Земельный участок, расположенный по адресу: "
              "Ленинградская область, Волосовский район, д Прологи.",
        address=None, region_name=None, bulk=True,
    )
    assert result and result.lat == 59.0
    assert calls
    assert all("Ленинградская область" in call for call in calls)
