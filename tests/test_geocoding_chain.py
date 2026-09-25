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

    def address_search(_query, **_kwargs):
        calls.append("address")
        return address

    monkeypatch.setattr("bankrotai.geo.IK12_GEOCODER.search_by_cadastral_number", ik12_search)
    monkeypatch.setattr("bankrotai.geo.CADASTRAL_GEOCODER._search_nspd_geoportal", nspd_search)
    monkeypatch.setattr("bankrotai.geo.CADASTRAL_GEOCODER.search_by_address", address_search)
    return calls


def test_nspd_success_stops_slow_fallback(monkeypatch) -> None:
    calls = install(monkeypatch, nspd=result("nspd"), ik12=result("ik12_cadastral"))
    resolved = resolve_lot_geo(CAD, ADDRESS, region_name="Ярославская область")
    assert resolved.source == "nspd"
    assert "5а/17" in resolved.address
    assert calls == ["nspd"]


def test_ik12_is_interactive_fallback_after_nspd(monkeypatch) -> None:
    calls = install(monkeypatch, ik12=result("ik12_cadastral"))
    assert resolve_lot_geo(CAD, ADDRESS, region_name="Ярославская область").source == "ik12_cadastral"
    assert calls == ["nspd", "ik12"]


def test_address_is_second_fallback(monkeypatch) -> None:
    calls = install(monkeypatch, address=result("nominatim", cad=None))
    assert resolve_lot_geo(CAD, ADDRESS, region_name="Ярославская область").source == "nominatim"
    assert calls == ["nspd", "ik12", "address"]


def test_suspicious_ik12_coordinate_is_rejected(monkeypatch) -> None:
    calls = install(
        monkeypatch,
        nspd=result("nspd", lat=55.7558, lon=37.6176),
        ik12=result("ik12_cadastral"),
    )
    resolved = resolve_lot_geo(CAD, ADDRESS, region_name="Ярославская область")
    assert resolved.source == "ik12_cadastral"
    assert resolved.attempts[0]["reason"] == "city_distance_mismatch"
    assert calls == ["nspd", "ik12"]


def test_all_providers_fail_with_explicit_status(monkeypatch) -> None:
    calls = install(monkeypatch)
    resolved = resolve_lot_geo(CAD, ADDRESS, region_name="Ярославская область")
    assert resolved.status == "GEOCODING_FAILED"
    assert resolved.confidence == "none"
    assert calls == ["nspd", "ik12", "address"]


def test_bulk_mode_skips_slow_fallbacks(monkeypatch) -> None:
    calls = install(monkeypatch)
    resolved = resolve_lot_geo(CAD, ADDRESS, region_name="Ярославская область", bulk=True)
    assert resolved.status == "GEOCODING_FAILED"
    assert calls == ["nspd", "address"]


def test_bulk_mode_tries_alternate_cadastral_numbers_before_address(monkeypatch) -> None:
    alternate = "76:23:050309:2209"
    calls: list[str] = []

    def nspd_search(query: str):
        calls.append(query)
        if query != alternate:
            return None
        return CadastralObjectResult(
            query=query,
            cadastral_number=alternate,
            lat=57.6291139,
            lon=39.8828543,
            source="nspd",
            confidence="high",
        )

    monkeypatch.setattr("bankrotai.geo.CADASTRAL_GEOCODER._search_nspd_geoportal", nspd_search)
    monkeypatch.setattr(
        "bankrotai.geo.CADASTRAL_GEOCODER.search_by_address",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("address fallback should not run")),
    )

    resolved = resolve_lot_geo(
        CAD,
        ADDRESS,
        cadastral_numbers=[CAD, alternate, "invalid"],
        region_name="Ярославская область",
        bulk=True,
    )

    assert resolved is not None
    assert resolved.source == "nspd"
    assert resolved.cadastral_number == alternate
    assert calls == [CAD, alternate]
    assert resolved.attempts[0] == {
        "source": "nspd_cadastral",
        "valid": False,
        "reason": "no_coordinates",
        "candidate_index": 0,
    }
    assert resolved.attempts[1] == {
        "source": "nspd_cadastral_alt",
        "valid": True,
        "reason": "validated",
        "candidate_index": 1,
    }


def test_yaroslavl_regression_rejects_other_region() -> None:
    valid, reason = validate_geocoding_result(
        result("legacy", lat=56.3269, lon=44.0059),
        cadastral_number=CAD,
        address=ADDRESS,
        region_name="Ярославская область",
    )
    assert valid is False
    assert reason == "city_distance_mismatch"


def test_cadastral_region_rejects_conflicting_address_without_region_field() -> None:
    valid, reason = validate_geocoding_result(
        CadastralObjectResult(
            query="г Ярославль, ул 2-я Тверицкая, д 13",
            lat=57.6385053, lon=39.9134741, source="photon", confidence="medium",
            address="\u042f\u0440\u043e\u0441\u043b\u0430\u0432\u043b\u044c, \u042f\u0440\u043e\u0441\u043b\u0430\u0432\u0441\u043a\u0430\u044f \u043e\u0431\u043b\u0430\u0441\u0442\u044c",
        ),
        cadastral_number="50:16:0102015:1318",
        address="г Ярославль, ул 2-я Тверицкая, д 13",
        region_name=None,
    )

    assert valid is False
    assert reason == "result_cadastral_region_mismatch"


def test_village_lot_rejects_a_different_locality_in_same_region() -> None:
    valid, reason = validate_geocoding_result(
        CadastralObjectResult(
            query="x", lat=57.6396394, lon=39.9670375, source="photon", confidence="high",
            address="\u041a\u0440\u0430\u0441\u043d\u044b\u0439 \u0411\u043e\u0440, \u042f\u0440\u043e\u0441\u043b\u0430\u0432\u0441\u043a\u0430\u044f \u043e\u0431\u043b\u0430\u0441\u0442\u044c",
        ),
        cadastral_number="76:17:204401:372",
        address="\u042f\u0440\u043e\u0441\u043b\u0430\u0432\u0441\u043a\u0430\u044f \u043e\u0431\u043b\u0430\u0441\u0442\u044c, \u042f\u0440\u043e\u0441\u043b\u0430\u0432\u0441\u043a\u0438\u0439 \u0440\u0430\u0439\u043e\u043d, \u0434. \u0413\u0443\u0431\u0446\u0435\u0432\u043e",
        region_name="\u042f\u0440\u043e\u0441\u043b\u0430\u0432\u0441\u043a\u0430\u044f \u043e\u0431\u043b\u0430\u0441\u0442\u044c",
    )

    assert valid is False
    assert reason == "locality_name_mismatch"


def test_cfo_coordinate_guard_rejects_far_east_outlier_without_result_address() -> None:
    valid, reason = validate_geocoding_result(
        CadastralObjectResult(
            query="50:00:0000000:1",
            cadastral_number="50:00:0000000:1",
            lat=55.5,
            lon=105.0,
            source="nspd",
            confidence="high",
            address=None,
        ),
        cadastral_number="50:00:0000000:1",
        address=None,
        region_name="Московская область",
    )

    assert valid is False
    assert reason == "cfo_region_bounds_mismatch"


def test_cfo_coordinate_guard_keeps_wide_valid_cfo_envelope() -> None:
    valid, reason = validate_geocoding_result(
        CadastralObjectResult(
            query="44:00:0000000:1",
            cadastral_number="44:00:0000000:1",
            lat=58.8,
            lon=47.0,
            source="nspd",
            confidence="high",
            address=None,
        ),
        cadastral_number="44:00:0000000:1",
        address=None,
        region_name="Костромская область",
    )

    assert valid is True
    assert reason == "validated"


def test_non_cfo_coordinate_is_not_subject_to_cfo_envelope() -> None:
    valid, reason = validate_geocoding_result(
        CadastralObjectResult(
            query="25:00:0000000:1",
            cadastral_number="25:00:0000000:1",
            lat=43.1,
            lon=131.9,
            source="nspd",
            confidence="high",
            address=None,
        ),
        cadastral_number="25:00:0000000:1",
        address=None,
        region_name="Приморский край",
    )

    assert valid is True
    assert reason == "validated"
