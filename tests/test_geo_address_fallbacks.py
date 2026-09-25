from __future__ import annotations

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
