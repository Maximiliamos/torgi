from __future__ import annotations

from bankrotai.geo import CadastralObjectResult, resolve_lot_geo, validate_geocoding_result


CAD = "76:23:050309:1108"
ADDRESS = "г. Ярославль, ул. Свердлова, д. 5а/17"


def result(source: str, lat: float = 57.6291139, lon: float = 39.8828543, *, cad: str | None = CAD):
    return CadastralObjectResult(
        query=CAD,
        cadastral_number=cad,
        lat=lat,
        lon=lon,
        source=source,
        confidence="high",
    )


def install(monkeypatch, *, ik12=None, nspd=None, address=None):
    calls: list[str] = []

    def ik12_search(_query):
        calls.append("ik12")
        return ik12

    def nspd_search(_query):
        calls.append("nspd")
        return nspd

    def address_search(_query):
        calls.append("address")
        return address

    monkeypatch.setattr("bankrotai.geo.IK12_GEOCODER.search_by_cadastral_number", ik12_search)
    monkeypatch.setattr("bankrotai.geo.CADASTRAL_GEOCODER._search_nspd_geoportal", nspd_search)
    monkeypatch.setattr("bankrotai.geo.CADASTRAL_GEOCODER.search_by_address", address_search)
    return calls


def test_ik12_success_stops_fallback(monkeypatch) -> None:
    calls = install(monkeypatch, ik12=result("ik12_cadastral"))
    resolved = resolve_lot_geo(CAD, ADDRESS, region_name="Ярославская область")
    assert resolved.source == "ik12_cadastral"
    assert calls == ["ik12"]


def test_nspd_is_first_fallback(monkeypatch) -> None:
    calls = install(monkeypatch, nspd=result("nspd"))
    assert resolve_lot_geo(CAD, ADDRESS, region_name="Ярославская область").source == "nspd"
    assert calls == ["ik12", "nspd"]


def test_address_is_second_fallback(monkeypatch) -> None:
    calls = install(monkeypatch, address=result("nominatim", cad=None))
    assert resolve_lot_geo(CAD, ADDRESS, region_name="Ярославская область").source == "nominatim"
    assert calls == ["ik12", "nspd", "address"]


def test_suspicious_ik12_coordinate_is_rejected(monkeypatch) -> None:
    calls = install(
        monkeypatch,
        ik12=result("ik12_cadastral", lat=55.7558, lon=37.6176),
        nspd=result("nspd"),
    )
    resolved = resolve_lot_geo(CAD, ADDRESS, region_name="Ярославская область")
    assert resolved.source == "nspd"
    assert resolved.attempts[0]["reason"] == "city_distance_mismatch"
    assert calls == ["ik12", "nspd"]


def test_all_providers_fail_with_explicit_status(monkeypatch) -> None:
    calls = install(monkeypatch)
    resolved = resolve_lot_geo(CAD, ADDRESS, region_name="Ярославская область")
    assert resolved.status == "GEOCODING_FAILED"
    assert resolved.confidence == "none"
    assert calls == ["ik12", "nspd", "address"]


def test_yaroslavl_regression_rejects_other_region() -> None:
    valid, reason = validate_geocoding_result(
        result("legacy", lat=56.3269, lon=44.0059),
        cadastral_number=CAD,
        address=ADDRESS,
        region_name="Ярославская область",
    )
    assert valid is False
    assert reason == "city_distance_mismatch"
