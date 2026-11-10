"""Broad offline regressions for property-address evidence, not five ID fixes.

All fixtures are synthetic/reduced address patterns without personal data.
Providers are mocked; no network calls or production DB writes.
"""
from __future__ import annotations

import pytest

import bankrotai.geo as geo
from bankrotai.geo import CadastralObjectResult, build_geocoding_address_candidates
from bankrotai.services.geo_address_candidates import extract_explicit_source_coordinates
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
        (
            "Земельный участок. Кадастровый номер: 02:53:120401:62. "
            "Местоположение установлено относительно ориентира, расположенного "
            "в границах участка. Почтовый адрес ориентира: Республика Башкортостан, "
            "р-н. Шаранский, с. Сюньбаш, ул. Центральная, д. 16. Площадь: 3288",
            "02:53:120401:62",
            "Республика Башкортостан, р-н. Шаранский, с. Сюньбаш, "
            "ул. Центральная, д. 16",
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

def test_cadastral_region_rejects_conflicting_stored_generic_address():
    stored = "г. Москва, ул. Тверская, д. 1"
    title = (
        "Земельный участок, расположенный по адресу: Тюменская область, "
        "Тюменский муниципальный округ, с. Луговое"
    )
    chosen = prefer_evidenced_property_address(
        stored,
        title=title,
        cadastral_number="72:17:1313001:252",
    )
    assert chosen == (
        "Тюменская область, Тюменский муниципальный округ, с. Луговое"
    )
    assert "Москва" not in chosen



def test_mets_official_asset_address_excludes_trailing_classification():
    # Reduced from the supplied public M-ETS 238546-1 detail card.
    description = (
        "Гараж, площадь: 20кв.м., адрес (местонахождение): Российская "
        "Федерация, Республика Хакасия, г.о. город Черногорск, "
        "г. Черногорск, гск В районе ПКСО общество Автолюбитель, "
        "влд. 386, категория земель: Нежилое здание, "
        "кадастровый номер: 19:02:010716:2557"
    )
    address = extract_asset_property_address(
        description=description, cadastral_number="19:02:010716:2557",
    )
    assert address == (
        "Российская Федерация, Республика Хакасия, г.о. город Черногорск, "
        "г. Черногорск, гск В районе ПКСО общество Автолюбитель, влд. 386"
    )
    assert "категория земель" not in address
    assert "кадастровый номер" not in address


def test_torgi_gov_dash_label_and_semicolon_metadata_are_parsed():
    # Reduced from the supplied public GIS Torgi land-lot card.
    description = (
        "адрес (местоположение) – Тюменская область, Тюменский муниципальный "
        "округ, с. Луговое (проект межевания территории Кулаковского сельского "
        "поселения Тюменского муниципального района, ЗУ252); "
        "кадастровый номер – 72:17:1102003:1802; площадь – 1072 кв.м; "
        "вид разрешенного использования - для индивидуального жилищного строительства"
    )
    address = extract_asset_property_address(
        description=description, cadastral_number="72:17:1102003:1802",
    )
    assert address == (
        "Тюменская область, Тюменский муниципальный округ, с. Луговое "
        "(проект межевания территории Кулаковского сельского поселения "
        "Тюменского муниципального района, ЗУ252)"
    )
    assert "кадастровый номер" not in address.casefold()
    assert "1072" not in address


def test_resolver_repairs_stale_listing_region_when_asset_and_cadastre_agree(monkeypatch):
    calls = []
    monkeypatch.setattr(
        geo.CADASTRAL_GEOCODER, "_search_nspd_geoportal",
        lambda *_args, **_kwargs: None,
    )

    def local_geocoder(address: str, *, allow_nominatim: bool = True):
        calls.append(address)
        assert "Московская область" in address
        return CadastralObjectResult(
            query=address,
            address="Московская область, Раменский район, д. Першино",
            lat=55.3,
            lon=38.2,
            source="photon",
            confidence="high",
        )

    monkeypatch.setattr(geo.CADASTRAL_GEOCODER, "search_by_address", local_geocoder)
    result = geo.resolve_lot_geo(
        cadastral_number="50:23:0050563:2759",
        address="Российская Федерация, Московская область, р-н Раменский, д. Першино",
        title="Земельный участок 600 м², Московская обл., Раменский р-н, д. Першино",
        description=(
            "Земельный участок площадью 600 кв.м, кадастровый номер "
            "50:23:0050563:2759. Адрес: Российская Федерация, Московская область, "
            "р-н Раменский, д. Першино"
        ),
        region_name="Ярославская область",  # stale listing-page hint
        region_code="50",
        bulk=True,
    )
    assert result and result.lat == 55.3
    assert calls


def test_explicit_source_coordinates_are_used_before_provider_lookup_without_cadastre(monkeypatch):
    address = (
        "Ярославская область, Ростовский р-н, вблизи деревни Краснораменье, "
        "56.997880; 38.924942"
    )
    assert extract_explicit_source_coordinates(address) == (56.99788, 38.924942)

    monkeypatch.setattr(
        geo.CADASTRAL_GEOCODER, "_search_nspd_geoportal",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("NSPD should not be called")),
    )
    monkeypatch.setattr(
        geo.CADASTRAL_GEOCODER, "search_by_address",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("Photon should not be called")),
    )
    result = geo.resolve_lot_geo(
        cadastral_number=None,
        address=address,
        region_name="Ярославская область",
        bulk=True,
    )
    assert result is not None
    assert result.source == "source_coordinates"
    assert result.lat == 56.99788
    assert result.lon == 38.924942
    assert result.attempts[-1]["reason"] == "validated"


def test_explicit_source_coordinates_do_not_bypass_cadastral_identity(monkeypatch):
    address = (
        "Ярославская область, Ростовский р-н, вблизи деревни Краснораменье, "
        "56.997880; 38.924942"
    )
    expected = "76:13:031301:92"
    nspd = CadastralObjectResult(
        query=expected,
        cadastral_number=expected,
        address="Ярославская область, Ростовский р-н, деревня Краснораменье",
        lat=56.99791,
        lon=38.92497,
        source="nspd",
        confidence="high",
    )
    monkeypatch.setattr(
        geo.CADASTRAL_GEOCODER, "_search_nspd_geoportal",
        lambda value, *_args, **_kwargs: nspd if value == expected else None,
    )
    result = geo.resolve_lot_geo(
        cadastral_number=expected,
        address=address,
        region_name="Ярославская область",
        bulk=True,
    )
    assert result is nspd
    assert result.source == "nspd"
    assert result.attempts[0] == {
        "source": "source_coordinates",
        "valid": False,
        "reason": "cadastral_confirmation_required",
    }
    assert result.attempts[-1]["source"] == "nspd_cadastral"
    assert result.attempts[-1]["reason"] == "validated"


def test_historical_lot_online_stale_seller_address_is_replaced_for_geocoding() -> None:
    stored = "190000, Санкт-Петербург, пер.Гривцова, д.5, литер. В"
    description = (
        "Государственное имущество: нежилые помещения, расположенные по адресу: "
        "Российская Федерация, Республика Коми, Муниципальный район Сосногорск, "
        "г.п. Сосногорск, п. Вис, ул. Ленина, д. 11/2, кадастровый номер "
        "11:19:0801020:1102"
    )
    preferred = prefer_evidenced_property_address(
        stored,
        description=description,
        cadastral_number="11:19:0801020:1102",
    )
    assert preferred is not None
    assert "Республика Коми" in preferred
    assert "Гривцова" not in preferred
    candidates = build_geocoding_address_candidates(
        stored,
        description=description,
        cadastral_number="11:19:0801020:1102",
    )
    assert candidates
    assert "Республика Коми" in candidates[0]
    assert all("Гривцова" not in value for value in candidates)


def test_legacy_cadastral_district_does_not_create_false_region_mismatch() -> None:
    result = CadastralObjectResult(
        query="85:01:091106:59",
        cadastral_number="85:01:091106:59",
        address="Иркутская область, Аларский район",
        lat=53.0,
        lon=103.0,
        source="nspd",
        confidence="high",
    )
    valid, reason = geo.validate_geocoding_result(
        result,
        cadastral_number="85:01:091106:59",
        address="Иркутская область, Аларский район",
        region_name="Иркутская область",
        region_code="38",
    )
    assert valid is True
    assert reason == "validated"
