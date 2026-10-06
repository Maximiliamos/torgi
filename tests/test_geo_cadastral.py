from bankrotai.geo import CadastralGeocoder, CadastralObjectResult, NSPDTLSVerificationError, PhotonGeocoder


def test_parse_pkk_feature_returns_result() -> None:
    geocoder = CadastralGeocoder()
    result = geocoder._parse_pkk_feature(
        {
            "features": [
                {
                    "attrs": {
                        "cn": "76:23:010101:15008",
                        "address": "г. Ярославль, Ленинградский пр-т, д. 105",
                        "type_name": "Здание",
                        "area_value": "123.4",
                    },
                    "center": {"x": 39.8845, "y": 57.6261},
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [
                            [
                                [39.884, 57.626],
                                [39.885, 57.626],
                                [39.885, 57.627],
                                [39.884, 57.626],
                            ]
                        ],
                    },
                }
            ]
        },
        "76:23:010101:15008",
        "building",
    )

    assert result is not None
    assert result.source == "pkk"
    assert result.cadastral_number == "76:23:010101:15008"
    assert result.lat == 57.6261
    assert result.lon == 39.8845
    assert result.has_boundary is True



def test_selected_address_resolves_photon_point_to_cadastral_object(monkeypatch) -> None:
    geocoder = CadastralGeocoder()
    address = "Ярославль, Ленинградский проспект, д 105"
    point = CadastralObjectResult(
        query=address,
        title="Адрес найден",
        address=address,
        lat=57.691848,
        lon=39.771867,
        source="photon",
        confidence="high",
    )
    expected = CadastralObjectResult(
        query=address,
        cadastral_number="76:23:010101:15008",
        object_type="Здание",
        address=address,
        lat=57.691848,
        lon=39.771867,
        source="nspd_wms",
        confidence="high",
        info={"Кадастровый номер": "76:23:010101:15008", "Адрес": address},
    )
    monkeypatch.setattr(geocoder, "search_by_address", lambda _query, **_kwargs: point)
    monkeypatch.setattr(
        geocoder,
        "search_objects_by_point",
        lambda lat, lon, **_kwargs: [expected] if (lat, lon) == (57.691848, 39.771867) else [],
    )

    result = geocoder.search_selected_address(address)

    assert result.cadastral_number == "76:23:010101:15008"
    assert result.source == "nspd_wms"
    assert result.address == address


def test_photon_address_suggestions_are_deduplicated(monkeypatch) -> None:
    class Response:
        def raise_for_status(self) -> None:
            return None

        def json(self):
            feature = {
                "geometry": {"coordinates": [39.771867, 57.691848]},
                "properties": {
                    "city": "Ярославль",
                    "street": "Ленинградский проспект",
                    "housenumber": "105",
                    "state": "Ярославская область",
                },
            }
            return {"features": [feature, feature]}

    monkeypatch.setattr("bankrotai.geo.require_provider", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("bankrotai.geo.requests.get", lambda *_args, **_kwargs: Response())
    geocoder = PhotonGeocoder("http://photon")

    result = geocoder.suggest_addresses("Ленинградский 105")

    assert result == [
        {
            "label": "Ярославль, Ленинградский проспект, д 105, Ярославская область",
            "lat": 57.691848,
            "lon": 39.771867,
        }
    ]



def test_interactive_cadastral_search_falls_back_to_ik12_when_nspd_tls_fails(monkeypatch) -> None:
    geocoder = CadastralGeocoder()
    expected = CadastralObjectResult(
        query="76:23:011401:8268",
        cadastral_number="76:23:011401:8268",
        object_type="Здание",
        address="г. Ярославль, Ленинградский проспект, д. 54а",
        lat=57.69072,
        lon=39.77901,
        source="ik12_cadastral",
        confidence="high",
        info={
            "Вид объекта недвижимости": "Здание",
            "Кадастровый номер": "76:23:011401:8268",
            "Адрес": "г. Ярославль, Ленинградский проспект, д. 54а",
        },
    )

    monkeypatch.setattr(geocoder, "_search_pkk_feature", lambda *_args: None)
    monkeypatch.setattr(
        geocoder,
        "_search_nspd_geoportal",
        lambda _query, **_kwargs: (_ for _ in ()).throw(NSPDTLSVerificationError("tls")),
    )
    monkeypatch.setattr(
        "bankrotai.geo.IK12_GEOCODER.search_by_cadastral_number",
        lambda _query, **_kwargs: expected,
    )

    result = geocoder.search_by_cadastral_number("76:23:011401:8268")

    assert result is expected
    assert result.address == "г. Ярославль, Ленинградский проспект, д. 54а"


def test_interactive_cadastral_search_reports_failure_only_after_ik12_fallback(monkeypatch) -> None:
    geocoder = CadastralGeocoder()
    calls: list[str] = []

    monkeypatch.setattr(geocoder, "_search_pkk_feature", lambda *_args: None)
    monkeypatch.setattr(
        geocoder,
        "_search_nspd_geoportal",
        lambda _query, **_kwargs: (_ for _ in ()).throw(NSPDTLSVerificationError("tls")),
    )

    def ik12_search(query: str, **_kwargs):
        calls.append(query)
        return None

    monkeypatch.setattr("bankrotai.geo.IK12_GEOCODER.search_by_cadastral_number", ik12_search)

    result = geocoder.search_by_cadastral_number("76:23:011401:8268")

    assert calls == ["76:23:011401:8268"]
    assert result.confidence == "none"
    assert result.source == "nspd/ik12"
    assert "резервный кадастровый источник" in str(result.error)



def test_photon_reverse_address_returns_local_label(monkeypatch) -> None:
    class Response:
        def raise_for_status(self) -> None:
            return None

        def json(self):
            return {
                "features": [
                    {
                        "properties": {
                            "city": "Ярославль",
                            "street": "Ленинградский проспект",
                            "housenumber": "54а",
                            "state": "Ярославская область",
                        }
                    }
                ]
            }

    calls: list[tuple[tuple, dict]] = []
    monkeypatch.setattr("bankrotai.geo.require_provider", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("bankrotai.geo.record_provider_success", lambda *_args, **_kwargs: None)

    def get(*args, **kwargs):
        calls.append((args, kwargs))
        return Response()

    monkeypatch.setattr("bankrotai.geo.requests.get", get)
    geocoder = PhotonGeocoder("http://photon")

    result = geocoder.reverse_address(57.69072, 39.77901)

    assert result == "Ярославль, Ленинградский проспект, д 54а, Ярославская область"
    assert calls[0][0][0] == "http://photon/reverse"
    assert calls[0][1]["params"]["lat"] == 57.69072
    assert calls[0][1]["params"]["lon"] == 39.77901


def test_exact_cadastral_result_fills_address_and_reuses_cache(monkeypatch) -> None:
    geocoder = CadastralGeocoder()
    calls: list[str] = []

    def nspd(query: str, **_kwargs):
        calls.append(query)
        return CadastralObjectResult(
            query=query,
            cadastral_number=query,
            object_type="Здание",
            lat=57.69072,
            lon=39.77901,
            source="nspd",
            confidence="high",
            info={"Кадастровый номер": query},
        )

    monkeypatch.setattr(geocoder, "_search_nspd_geoportal", nspd)
    monkeypatch.setattr(
        "bankrotai.geo.PHOTON_GEOCODER.reverse_address",
        lambda _lat, _lon: "Ярославль, Ленинградский проспект, д 54а",
    )

    first = geocoder.search_by_cadastral_number("76:23:011401:8268")
    first.address = "mutated outside cache"
    second = geocoder.search_by_cadastral_number("76:23:011401:8268")

    assert calls == ["76:23:011401:8268"]
    assert second.address == "Ярославль, Ленинградский проспект, д 54а"
    assert second.info["Адрес"] == "Ярославль, Ленинградский проспект, д 54а"


def test_nspd_point_lookup_keeps_tls_verification_and_building_priority(monkeypatch) -> None:
    class Response:
        def raise_for_status(self) -> None:
            return None

        def json(self):
            return {
                "features": [
                    {
                        "properties": {
                            "options": {"cad_num": "76:23:010101:15008"},
                            "categoryName": "Здание",
                        },
                        "geometry": {
                            "type": "Polygon",
                            "coordinates": [
                                [
                                    [39.7717, 57.6917],
                                    [39.7722, 57.6917],
                                    [39.7722, 57.6921],
                                    [39.7717, 57.6917],
                                ]
                            ],
                        },
                    }
                ]
            }

    calls: list[dict] = []
    monkeypatch.setattr("bankrotai.geo.require_provider", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("bankrotai.geo.record_provider_success", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("bankrotai.geo.nspd_tls_verify", lambda: "/trusted/ca.pem")

    def get(_url, **kwargs):
        calls.append(kwargs)
        return Response()

    monkeypatch.setattr("bankrotai.geo.requests.get", get)
    geocoder = CadastralGeocoder()

    result = geocoder._search_nspd_by_point(
        57.6919301,
        39.7720143,
        fallback_address="Ярославль, Ленинградский проспект, д 105",
    )

    assert result is not None
    assert result.cadastral_number == "76:23:010101:15008"
    assert result.address == "Ярославль, Ленинградский проспект, д 105"
    assert result.has_boundary is True
    assert result.source == "nspd_wms"
    assert calls[0]["params"]["LAYERS"] == "36049"
    assert calls[0]["verify"] == "/trusted/ca.pem"


def test_interactive_nspd_connect_failure_does_not_retry(monkeypatch) -> None:
    import pytest
    import requests
    from bankrotai.services.geo_resilience import GeoProviderUnavailable

    calls = 0
    monkeypatch.setattr("bankrotai.geo.require_provider", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("bankrotai.geo.record_provider_failure", lambda *_args, **_kwargs: None)

    def get(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise requests.ConnectTimeout("transient")

    monkeypatch.setattr("bankrotai.geo.requests.get", get)
    geocoder = CadastralGeocoder()

    with pytest.raises(GeoProviderUnavailable):
        geocoder._search_nspd_geoportal("76:23:011401:8268", interactive=True)

    assert calls == 1


def test_interactive_nspd_retries_read_timeout_once_when_budget_remains(monkeypatch) -> None:
    import requests

    class Response:
        def raise_for_status(self) -> None:
            return None

        def json(self):
            return {
                "data": {
                    "features": [
                        {
                            "properties": {
                                "options": {"cad_num": "76:23:011401:8268"},
                                "categoryName": "Здание",
                            },
                            "geometry": {"type": "Point", "coordinates": [39.77901, 57.69072]},
                        }
                    ]
                }
            }

    calls = 0
    monkeypatch.setattr("bankrotai.geo.require_provider", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("bankrotai.geo.record_provider_failure", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("bankrotai.geo.record_provider_success", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("bankrotai.geo.time.sleep", lambda *_args: None)

    def get(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise requests.ReadTimeout("transient")
        return Response()

    monkeypatch.setattr("bankrotai.geo.requests.get", get)
    geocoder = CadastralGeocoder()
    geocoder._nspd_disabled_until = 10**12

    result = geocoder._search_nspd_geoportal("76:23:011401:8268", interactive=True)

    assert calls == 2
    assert result is not None
    assert result.cadastral_number == "76:23:011401:8268"
    assert geocoder._nspd_disabled_until == 0.0



def test_selected_address_does_not_use_legacy_pkk_when_nspd_wms_is_unavailable(monkeypatch) -> None:
    geocoder = CadastralGeocoder()
    address = "Ярославль, Ленинградский проспект, д 105"
    point = CadastralObjectResult(
        query=address,
        title="Адрес найден",
        address=address,
        lat=57.6919301,
        lon=39.7720143,
        source="photon",
        confidence="high",
    )
    monkeypatch.setattr(geocoder, "search_by_address", lambda _query, **_kwargs: point)
    monkeypatch.setattr(
        geocoder,
        "search_objects_by_point",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(NSPDTLSVerificationError("tls")),
    )
    monkeypatch.setattr(
        geocoder,
        "_search_pkk_by_point",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("legacy PKK must not run")),
    )

    result = geocoder.search_selected_address(address)

    assert result.cadastral_number is None
    assert result.source == "photon"



def test_normalize_nspd_props_supports_current_snake_case_options() -> None:
    from bankrotai.geo import normalize_nspd_props

    info = normalize_nspd_props(
        {
            "category_name": "Здания",
            "options": {
                "cad_num": "76:23:011401:8268",
                "readable_address": "г. Ярославль, Ленинградский проспект, д. 54а",
                "specified_area": 19513.2,
                "cost_value": 670868499.17,
                "floors": 3,
                "year_built": 2018,
                "no_coords": False,
            },
        },
        "76:23:011401:8268",
    )

    assert info["Кадастровый номер"] == "76:23:011401:8268"
    assert info["Адрес"] == "г. Ярославль, Ленинградский проспект, д. 54а"
    assert info["Площадь общая"] == 19513.2
    assert info["Кадастровая стоимость"] == 670868499.17
    assert info["Количество этажей"] == 3
    assert info["Завершение строительства"] == 2018


def test_nspd_wms_returns_all_unique_cadastral_objects(monkeypatch) -> None:
    class Response:
        def __init__(self, layer: str):
            self.layer = layer

        def raise_for_status(self) -> None:
            return None

        def json(self):
            number = "76:23:010101:15008" if self.layer == "36049" else "76:23:010101:123"
            return {
                "features": [
                    {
                        "properties": {
                            "category_name": "Здания" if self.layer == "36049" else "Земельные участки из ЕГРН",
                            "options": {"cad_num": number},
                        },
                        "geometry": {"type": "Point", "coordinates": [39.7720143, 57.6919301]},
                    }
                ]
            }

    monkeypatch.setattr("bankrotai.geo.require_provider", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("bankrotai.geo.record_provider_success", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("bankrotai.geo.nspd_tls_verify", lambda: True)

    def get(url, **_kwargs):
        return Response("36049" if "/36049/" in url else "36048")

    monkeypatch.setattr("bankrotai.geo.requests.get", get)
    geocoder = CadastralGeocoder()

    result = geocoder.search_objects_by_point(
        57.6919301,
        39.7720143,
        fallback_address="Ярославль, Ленинградский проспект, д 105",
    )

    assert [item.cadastral_number for item in result] == [
        "76:23:010101:15008",
        "76:23:010101:123",
    ]
    assert all(item.address == "Ярославль, Ленинградский проспект, д 105" for item in result)



def test_exact_search_does_not_start_ik12_when_nspd_wins_before_hedge(monkeypatch) -> None:
    import bankrotai.geo as geo

    geocoder = CadastralGeocoder()
    result = CadastralObjectResult(
        query="76:23:011401:8268",
        cadastral_number="76:23:011401:8268",
        address="Ярославль",
        lat=57.69,
        lon=39.77,
        source="nspd",
        confidence="high",
    )
    calls: list[str] = []
    monkeypatch.setattr(geo, "INTERACTIVE_NSPD_HEDGE_DELAY_SECONDS", 0.2)
    monkeypatch.setattr(geo, "INTERACTIVE_CADASTRAL_HARD_DEADLINE_SECONDS", 0.5)
    monkeypatch.setattr(
        geocoder,
        "_search_nspd_geoportal",
        lambda *_args, **_kwargs: result,
    )
    monkeypatch.setattr(
        geo.IK12_GEOCODER,
        "search_by_cadastral_number",
        lambda *_args, **_kwargs: calls.append("ik12"),
    )

    observed = geocoder.search_by_cadastral_number("76:23:011401:8268")

    assert observed.source == "nspd"
    assert calls == []
    assert observed.attempts[0]["provider"] == "nspd"
    assert observed.attempts[0]["outcome"] == "success"


def test_exact_search_hedges_ik12_when_nspd_is_slow(monkeypatch) -> None:
    import time
    import bankrotai.geo as geo

    geocoder = CadastralGeocoder()
    nspd = CadastralObjectResult(
        query="76:23:011401:8268",
        cadastral_number="76:23:011401:8268",
        lat=57.69,
        lon=39.77,
        source="nspd",
        confidence="high",
    )
    ik12 = CadastralObjectResult(
        query="76:23:011401:8268",
        cadastral_number="76:23:011401:8268",
        address="Ярославль",
        lat=57.69,
        lon=39.77,
        source="ik12_cadastral",
        confidence="high",
    )
    monkeypatch.setattr(geo, "INTERACTIVE_NSPD_HEDGE_DELAY_SECONDS", 0.02)
    monkeypatch.setattr(geo, "INTERACTIVE_CADASTRAL_HARD_DEADLINE_SECONDS", 0.4)

    def slow_nspd(*_args, **_kwargs):
        time.sleep(0.12)
        return nspd

    def fast_ik12(*_args, **_kwargs):
        time.sleep(0.01)
        return ik12

    monkeypatch.setattr(geocoder, "_search_nspd_geoportal", slow_nspd)
    monkeypatch.setattr(geo.IK12_GEOCODER, "search_by_cadastral_number", fast_ik12)

    started = time.monotonic()
    observed = geocoder.search_by_cadastral_number("76:23:011401:8268")
    elapsed = time.monotonic() - started

    assert observed.source == "ik12_cadastral"
    assert elapsed < 0.11
    assert any(item["provider"] == "ik12" and item["outcome"] == "success" for item in observed.attempts)


def test_exact_search_starts_ik12_immediately_after_nspd_tls_failure(monkeypatch) -> None:
    import time
    import bankrotai.geo as geo

    geocoder = CadastralGeocoder()
    ik12 = CadastralObjectResult(
        query="76:23:011401:8268",
        cadastral_number="76:23:011401:8268",
        lat=57.69,
        lon=39.77,
        source="ik12_cadastral",
        confidence="high",
    )
    monkeypatch.setattr(geo, "INTERACTIVE_NSPD_HEDGE_DELAY_SECONDS", 0.3)
    monkeypatch.setattr(geo, "INTERACTIVE_CADASTRAL_HARD_DEADLINE_SECONDS", 0.5)
    monkeypatch.setattr(
        geocoder,
        "_search_nspd_geoportal",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(NSPDTLSVerificationError("tls")),
    )
    monkeypatch.setattr(
        geo.IK12_GEOCODER,
        "search_by_cadastral_number",
        lambda *_args, **_kwargs: ik12,
    )

    started = time.monotonic()
    observed = geocoder.search_by_cadastral_number("76:23:011401:8268")
    elapsed = time.monotonic() - started

    assert observed.source == "ik12_cadastral"
    assert elapsed < 0.2
    assert observed.attempts[0]["provider"] == "nspd"
    assert observed.attempts[0]["outcome"].startswith("error:")


def test_ik12_pow_respects_deadline() -> None:
    import time
    import pytest
    from bankrotai.geo import IK12Geocoder

    started = time.monotonic()
    with pytest.raises(TimeoutError, match="proof-of-work deadline"):
        IK12Geocoder._solve_pow(
            123,
            "76:23:011401:8268",
            0,
            deadline_monotonic=time.monotonic() + 0.02,
        )
    assert time.monotonic() - started < 0.25


def test_exact_search_hard_deadline_bounds_both_providers(monkeypatch) -> None:
    import time
    import bankrotai.geo as geo
    from bankrotai.services.geo_resilience import GeoProviderUnavailable

    geocoder = CadastralGeocoder()
    monkeypatch.setattr(geo, "INTERACTIVE_NSPD_HEDGE_DELAY_SECONDS", 0.01)
    monkeypatch.setattr(geo, "INTERACTIVE_CADASTRAL_HARD_DEADLINE_SECONDS", 0.08)

    def bounded_failure(*_args, deadline_monotonic=None, **_kwargs):
        while deadline_monotonic is not None and time.monotonic() < deadline_monotonic:
            time.sleep(0.005)
        raise GeoProviderUnavailable("test", "read_timeout", "deadline")

    monkeypatch.setattr(geocoder, "_search_nspd_geoportal", bounded_failure)
    monkeypatch.setattr(geo.IK12_GEOCODER, "search_by_cadastral_number", bounded_failure)

    started = time.monotonic()
    observed = geocoder.search_by_cadastral_number("76:23:011401:8268")
    elapsed = time.monotonic() - started

    assert elapsed < 0.2
    assert observed.confidence == "none"
    assert observed.source == "nspd/ik12"
    assert observed.attempts[-1]["provider"] == "exact_chain"
