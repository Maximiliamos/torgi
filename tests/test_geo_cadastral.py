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



def test_selected_address_prefers_cadastral_nspd_result(monkeypatch) -> None:
    geocoder = CadastralGeocoder()
    expected = CadastralObjectResult(
        query="Ярославль, Ленинградский проспект, д 105",
        cadastral_number="76:23:010101:15008",
        object_type="Здание",
        address="Ярославль, Ленинградский проспект, д 105",
        lat=57.691848,
        lon=39.771867,
        source="nspd",
        confidence="high",
        info={"Кадастровый номер": "76:23:010101:15008"},
    )
    monkeypatch.setattr(geocoder, "_search_nspd_geoportal", lambda _query: expected)
    monkeypatch.setattr(
        geocoder,
        "search_by_address",
        lambda _query: (_ for _ in ()).throw(AssertionError("fallback must not run")),
    )

    result = geocoder.search_selected_address("Ярославль, Ленинградский проспект, д 105")

    assert result is expected
    assert result.cadastral_number == "76:23:010101:15008"


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
        lambda _query: (_ for _ in ()).throw(NSPDTLSVerificationError("tls")),
    )
    monkeypatch.setattr(
        "bankrotai.geo.IK12_GEOCODER.search_by_cadastral_number",
        lambda _query: expected,
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
        lambda _query: (_ for _ in ()).throw(NSPDTLSVerificationError("tls")),
    )

    def ik12_search(query: str):
        calls.append(query)
        return None

    monkeypatch.setattr("bankrotai.geo.IK12_GEOCODER.search_by_cadastral_number", ik12_search)

    result = geocoder.search_by_cadastral_number("76:23:011401:8268")

    assert calls == ["76:23:011401:8268"]
    assert result.confidence == "none"
    assert result.source == "pkk/nspd/ik12"
    assert "резервный кадастровый источник" in str(result.error)
