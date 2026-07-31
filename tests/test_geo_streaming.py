from concurrent.futures import ThreadPoolExecutor

from bankrotai.geo import NominatimGeocoder, build_geocoding_address_candidates
from bankrotai.gui import MainWindow


RAD_ADDRESS = (
    "\u042f\u0440\u043e\u0441\u043b\u0430\u0432\u0441\u043a\u0430\u044f \u043e\u0431\u043b\u0430\u0441\u0442\u044c, \u043c. \u0440-\u043d \u0423\u0433\u043b\u0438\u0447\u0441\u043a\u0438\u0439, \u0441. \u043f. \u0421\u043b\u043e\u0431\u043e\u0434\u0441\u043a\u043e\u0435, "
    "\u0441. \u0415\u0444\u0440\u0435\u043c\u043e\u0432\u043e, \u0441 \u0437\u0435\u043c\u0435\u043b\u044c\u043d\u044b\u043c \u0443\u0447\u0430\u0441\u0442\u043a\u043e\u043c"
)


def test_rad_address_candidates_use_current_municipal_name() -> None:
    candidates = build_geocoding_address_candidates(RAD_ADDRESS)

    assert candidates[0] == (
        "\u042f\u0440\u043e\u0441\u043b\u0430\u0432\u0441\u043a\u0430\u044f \u043e\u0431\u043b\u0430\u0441\u0442\u044c, \u0423\u0433\u043b\u0438\u0447\u0441\u043a\u0438\u0439 \u043c\u0443\u043d\u0438\u0446\u0438\u043f\u0430\u043b\u044c\u043d\u044b\u0439 \u043e\u043a\u0440\u0443\u0433, \u0441\u0435\u043b\u043e \u0415\u0444\u0440\u0435\u043c\u043e\u0432\u043e"
    )
    assert "\u0415\u0444\u0440\u0435\u043c\u043e\u0432\u043e, \u0423\u0433\u043b\u0438\u0447\u0441\u043a\u0438\u0439 \u043c\u0443\u043d\u0438\u0446\u0438\u043f\u0430\u043b\u044c\u043d\u044b\u0439 \u043e\u043a\u0440\u0443\u0433" in candidates[1]
    assert all("\u0437\u0435\u043c\u0435\u043b\u044c\u043d\u044b\u043c \u0443\u0447\u0430\u0441\u0442\u043a\u043e\u043c" not in item for item in candidates)


def test_nominatim_selects_matching_district_not_first_namesake(monkeypatch) -> None:
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return [
                {
                    "lat": "57.6908332",
                    "lon": "39.5508893",
                    "importance": 0.8,
                    "display_name": (
                        "\u0415\u0444\u0440\u0435\u043c\u043e\u0432\u043e, \u042f\u0440\u043e\u0441\u043b\u0430\u0432\u0441\u043a\u0438\u0439 \u043c\u0443\u043d\u0438\u0446\u0438\u043f\u0430\u043b\u044c\u043d\u044b\u0439 \u043e\u043a\u0440\u0443\u0433, "
                        "\u042f\u0440\u043e\u0441\u043b\u0430\u0432\u0441\u043a\u0430\u044f \u043e\u0431\u043b\u0430\u0441\u0442\u044c, \u0420\u043e\u0441\u0441\u0438\u044f"
                    ),
                },
                {
                    "lat": "57.4653825",
                    "lon": "38.7351138",
                    "importance": 0.1,
                    "display_name": (
                        "\u0415\u0444\u0440\u0435\u043c\u043e\u0432\u043e, \u0423\u0433\u043b\u0438\u0447\u0441\u043a\u0438\u0439 \u043c\u0443\u043d\u0438\u0446\u0438\u043f\u0430\u043b\u044c\u043d\u044b\u0439 \u043e\u043a\u0440\u0443\u0433, "
                        "\u042f\u0440\u043e\u0441\u043b\u0430\u0432\u0441\u043a\u0430\u044f \u043e\u0431\u043b\u0430\u0441\u0442\u044c, \u0420\u043e\u0441\u0441\u0438\u044f"
                    ),
                },
            ]

    monkeypatch.setattr("bankrotai.geo.NOMINATIM_MIN_REQUEST_INTERVAL", 0)
    monkeypatch.setattr("bankrotai.geo.requests.get", lambda *args, **kwargs: Response())

    result = NominatimGeocoder().geocode(RAD_ADDRESS)

    assert result is not None
    assert result["centroid_lat"] == 57.4653825
    assert result["centroid_lon"] == 38.7351138


def test_generated_maps_support_incremental_lot_updates() -> None:
    class FakeWindow:
        cadastral_wms_proxy_port = 0

        @staticmethod
        def get_map_icon_urls():
            return {"land": "", "rent": "", "realEstate": "", "auto": "", "other": ""}

    fake = FakeWindow()
    leaflet_html = MainWindow.build_map_html(fake, [])
    yandex_html = MainWindow.build_yandex_map_html(fake, [])

    assert "window.upsertLot = upsertLot" in leaflet_html
    assert "lotMarkers" in leaflet_html
    assert "window.upsertLot = upsertLot" in yandex_html
    assert "lotPlacemarks" in yandex_html


def test_parallel_duplicate_addresses_share_one_request(monkeypatch) -> None:
    calls = []

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return [{"lat": "57.6", "lon": "39.8", "importance": 0.8}]

    def get(*args, **kwargs):
        calls.append((args, kwargs))
        return Response()

    monkeypatch.setattr("bankrotai.geo.NOMINATIM_MIN_REQUEST_INTERVAL", 0)
    monkeypatch.setattr("bankrotai.geo.requests.get", get)
    geocoder = NominatimGeocoder()

    with ThreadPoolExecutor(max_workers=6) as executor:
        results = list(executor.map(geocoder.geocode, [RAD_ADDRESS] * 12))

    assert all(result == results[0] for result in results)
    assert len(calls) == 1
