from __future__ import annotations

# Structured Photon candidates stay bounded and deterministic across provider-chain updates.

from bankrotai.geo import (
    CadastralObjectResult,
    build_geocoding_address_candidates,
    resolve_lot_geo,
)
import bankrotai.geo as geo


def test_address_candidates_include_exact_numbered_address_from_description() -> None:
    candidates = build_geocoding_address_candidates(
        "Московская область, городской округ Химки",
        title="Нежилое помещение",
        description=(
            "Объект расположен по адресу: г. Химки, ул. Молодежная, д. 12. "
            "Площадь 120 кв.м."
        ),
        region_name="Московская область",
    )

    assert candidates
    assert candidates[0].startswith("Московская область")
    assert any(
        "Химки" in candidate and "Молодежная" in candidate and "12" in candidate
        for candidate in candidates[1:]
    )


def test_bulk_resolver_tries_alternate_address_candidate_after_primary_miss(monkeypatch) -> None:
    calls: list[str] = []

    def fake_search(address: str, *, allow_nominatim: bool = True):
        calls.append(address)
        if "Молодежная" not in address:
            return CadastralObjectResult(
                query=address,
                address=address,
                source="photon",
                confidence="none",
                status="GEOCODING_FAILED",
            )
        return CadastralObjectResult(
            query=address,
            address="Химки, улица Молодежная, 12, Московская область",
            lat=55.8880,
            lon=37.4300,
            source="photon",
            confidence="high",
        )

    monkeypatch.setattr(geo.CADASTRAL_GEOCODER, "search_by_address", fake_search)

    result = resolve_lot_geo(
        address="Московская область, городской округ Химки",
        title="Нежилое помещение",
        description="Адрес: г. Химки, ул. Молодежная, д. 12.",
        region_name="Московская область",
        bulk=True,
    )

    assert result is not None
    assert result.lat == 55.8880
    assert result.lon == 37.4300
    assert len(calls) >= 2
    assert "Молодежная" not in calls[0]
    assert any("Молодежная" in call for call in calls[1:])
    assert result.attempts[-1]["source"] == "address_geocoder_alt"
    assert result.attempts[-1]["valid"] is True


def test_bulk_resolver_uses_structured_photon_candidate_without_public_fallback(monkeypatch) -> None:
    calls: list[tuple[str, bool]] = []

    def fake_search(address: str, *, allow_nominatim: bool = True):
        calls.append((address, allow_nominatim))
        # The full auction-card address misses, while the structured
        # street/locality candidate produced by the builder resolves locally.
        if not address.startswith("5, Центральная улица"):
            return None
        return CadastralObjectResult(
            query=address,
            address="Губцево, Центральная улица, 5, Ярославская область",
            lat=57.7133,
            lon=39.7156,
            source="photon",
            confidence="high",
        )

    monkeypatch.setattr(geo.CADASTRAL_GEOCODER, "search_by_address", fake_search)

    resolved = resolve_lot_geo(
        address=(
            "Ярославская область, Ярославский район, деревня Губцево, "
            "Центральная улица, дом 5"
        ),
        region_name="Ярославская область",
        bulk=True,
    )

    assert resolved is not None
    assert resolved.source == "photon"
    assert resolved.lat == 57.7133
    assert len(calls) == 2
    assert calls[0][1] is False
    assert calls[1][1] is False
    assert calls[1][0].startswith("5, Центральная улица")
    assert resolved.attempts[-1]["candidate_index"] == 1
    assert resolved.attempts[-1]["valid"] is True


def test_bulk_resolver_caps_local_photon_candidates(monkeypatch) -> None:
    calls: list[str] = []

    monkeypatch.setattr(
        geo.CADASTRAL_GEOCODER,
        "search_by_address",
        lambda address, **_kwargs: (calls.append(address), None)[1],
    )

    resolved = resolve_lot_geo(
        address=(
            "Ярославская область, Ярославский район, деревня Губцево, "
            "Центральная улица, дом 5"
        ),
        region_name="Ярославская область",
        bulk=True,
    )

    assert resolved is not None
    assert resolved.status == "GEOCODING_FAILED"
    assert len(calls) <= 3


def test_alternate_address_candidate_still_rejects_wrong_region(monkeypatch) -> None:
    def fake_search(address: str, *, allow_nominatim: bool = True):
        if "Молодежная" not in address:
            return None
        return CadastralObjectResult(
            query=address,
            address="Химки, улица Молодежная, 12, Ленинградская область",
            lat=59.90,
            lon=30.30,
            source="photon",
            confidence="high",
        )

    monkeypatch.setattr(geo.CADASTRAL_GEOCODER, "search_by_address", fake_search)

    result = resolve_lot_geo(
        address="Московская область, городской округ Химки",
        title="Нежилое помещение",
        description="Адрес: г. Химки, ул. Молодежная, д. 12.",
        region_name="Московская область",
        bulk=True,
    )

    assert result is not None
    assert result.status == "GEOCODING_FAILED"
    assert any(
        attempt["reason"] in {
            "result_cadastral_region_mismatch",
            "result_region_mismatch",
            "locality_name_mismatch",
            "city_distance_mismatch",
        }
        or attempt["valid"] is False
        for attempt in result.attempts
    )

def test_address_candidates_expand_building_parts_and_strip_unit_tail() -> None:
    candidates = build_geocoding_address_candidates(
        "г. Ярославль, ул. Свободы, д. 10, корп. 2, стр. 1, пом. 15",
        region_name="Ярославская область",
    )

    assert candidates
    assert "корпус 2" in candidates[0]
    assert "строение 1" in candidates[0]
    assert any("пом" not in candidate.casefold() and "дом 10" in candidate.casefold() for candidate in candidates)


def test_bulk_resolver_uses_cadastral_provider_address_when_geometry_is_missing(monkeypatch) -> None:
    cad = "76:23:010101:77"
    calls: list[tuple[str, bool]] = []

    monkeypatch.setattr(
        geo.CADASTRAL_GEOCODER,
        "_search_nspd_geoportal",
        lambda query: CadastralObjectResult(
            query=query,
            cadastral_number=cad,
            address="Ярославская область, город Ярославль, улица Свободы, дом 10",
            source="nspd",
            confidence="high",
        ),
    )

    def address_search(address: str, *, allow_nominatim: bool = True):
        calls.append((address, allow_nominatim))
        if "Свободы" not in address:
            return None
        return CadastralObjectResult(
            query=address,
            address="Ярославль, улица Свободы, 10, Ярославская область",
            lat=57.6261,
            lon=39.8845,
            source="photon",
            confidence="high",
        )

    monkeypatch.setattr(geo.CADASTRAL_GEOCODER, "search_by_address", address_search)

    resolved = resolve_lot_geo(
        cad,
        "Ярославская область, город Ярославль",
        region_name="Ярославская область",
        region_code="76",
        bulk=True,
    )

    assert resolved is not None
    assert resolved.lat == 57.6261
    assert resolved.lon == 39.8845
    assert calls[0][1] is False
    assert "Свободы" in calls[0][0]
    assert resolved.attempts[-1]["source"] == "address_geocoder_cadastral_hint"
    assert resolved.attempts[-1]["valid"] is True


def test_cadastral_provider_address_hint_is_ignored_for_wrong_cadastral_number(monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        geo.CADASTRAL_GEOCODER,
        "_search_nspd_geoportal",
        lambda query: CadastralObjectResult(
            query=query,
            cadastral_number="50:01:000000:1",
            address="Московская область, город Химки, улица Молодежная, дом 12",
            source="nspd",
            confidence="high",
        ),
    )
    monkeypatch.setattr(
        geo.CADASTRAL_GEOCODER,
        "search_by_address",
        lambda address, **_kwargs: (calls.append(address), None)[1],
    )

    result = resolve_lot_geo(
        "76:23:010101:77",
        "Ярославская область, город Ярославль",
        region_name="Ярославская область",
        region_code="76",
        bulk=True,
    )

    assert result is not None
    assert result.status == "GEOCODING_FAILED"
    assert all("Молодежная" not in call for call in calls)

