from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from types import SimpleNamespace

from bankrotai.geo import NominatimGeocoder, build_geocoding_address_candidates
from bankrotai.gui import (
    GeoWorker,
    MainWindow,
    PreviewEnrichmentWorker,
    extract_preview_image_url,
    map_assets_directory,
)
from PySide6.QtCore import QCoreApplication
from PySide6.QtTest import QSignalSpy


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


def test_existing_lot_online_title_can_supply_address_for_geocoding() -> None:
    candidates = build_geocoding_address_candidates(
        None,
        title="Квартира, Адрес (местоположение): Рязанская обл., г. Сасово, ул. Ленина, д. 7",
    )

    assert candidates
    assert "Сасово" in candidates[0]
    assert "улица Ленина" in candidates[0]


def test_description_and_region_can_supply_address_for_geocoding() -> None:
    candidates = build_geocoding_address_candidates(
        None,
        title="Продажа имущества",
        description="Адрес (местоположение): г. Рыбинск, ул. Ленина, д. 10",
        region_name="Ярославская область",
    )

    assert candidates
    assert "Рыбинск" in candidates[0]
    assert "Ярославская область" in candidates[0]


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

    fake = FakeWindow()
    leaflet_html = MainWindow.build_map_html(fake, [])
    yandex_html = MainWindow.build_yandex_map_html(fake, [])

    assert "window.upsertLot = upsertLot" in leaflet_html
    assert "lotMarkers" in leaflet_html
    assert "window.upsertLot = upsertLot" in yandex_html
    assert "lotPlacemarks" in yandex_html
    assert "initLeafletFallback" in yandex_html
    assert "typeof ymaps !== 'undefined'" in yandex_html
    assert "https://unpkg.com" not in leaflet_html
    assert 'href="leaflet.css"' in leaflet_html
    assert 'src="leaflet.js"' in leaflet_html
    assert (map_assets_directory() / "leaflet.js").is_file()
    assert (map_assets_directory() / "leaflet.markercluster.js").is_file()
    for html in (leaflet_html, yandex_html):
        assert 'id="lot-preview"' in html
        assert "showLotPreview(lot)" in html
        assert "bankrotaiBridge.setReviewStatus" in html
        assert 'data-status="approved"' in html
        assert 'data-status="maybe"' in html
        assert 'data-status="rejected"' in html
        assert 'id="lot-preview-prev"' in html
        assert 'data-url-key="gis_torgi_url"' in html
        assert 'data-url-key="etp_url"' in html
        assert 'data-url-key="torgi_russia_url"' in html
        assert "#7d8795" in html
        assert "window.applyLotReviewStatus" in html
        assert "qrc:///qtwebchannel/qwebchannel.js" in html
        assert "#111111" in html
        assert "isLotEnded" in html
    assert "noWrap: true" in leaflet_html
    assert "maxBounds: worldBounds" in leaflet_html
    assert "enforceSingleWorldZoom" not in yandex_html
    assert "restrictMapArea" not in yandex_html
    assert "tileLoaded" not in yandex_html
    assert "map.destroy()" not in yandex_html


def test_map_filters_use_price_and_inferred_region() -> None:
    lot = SimpleNamespace(
        current_price=2_500_000,
        start_price=3_000_000,
        region_name=None,
        cadastral_number="62:27:0010101:15",
        address=None,
        title="Нежилое помещение",
        description="",
    )

    assert MainWindow._lot_region_label(lot) == "Рязанская область"
    assert MainWindow._map_lot_matches_filters(
        lot,
        min_price=2_000_000,
        max_price=3_000_000,
        region="Рязанская область",
    )
    assert not MainWindow._map_lot_matches_filters(lot, min_price=3_000_001)
    assert not MainWindow._map_lot_matches_filters(lot, region="Ярославская область")


def test_preview_image_extraction_handles_source_payload_shapes() -> None:
    assert extract_preview_image_url({"image_url": "https://example.test/main.jpg"}) == "https://example.test/main.jpg"
    assert extract_preview_image_url({"photos": [{"thumbnail": "//example.test/thumb.jpg"}]}) == "https://example.test/thumb.jpg"
    assert extract_preview_image_url({"image_url": "file:///private/photo.jpg"}) is None


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


def test_preview_worker_is_retained_until_native_qthread_finishes(monkeypatch) -> None:
    class EmptySession:
        @staticmethod
        def get(_model, _lot_id):
            return None

    @contextmanager
    def empty_session_scope():
        yield EmptySession()

    monkeypatch.setattr("bankrotai.gui.session_scope", empty_session_scope)
    app = QCoreApplication.instance() or QCoreApplication([])
    worker = PreviewEnrichmentWorker(-1)
    native_finished = QSignalSpy(worker.finished)
    result_ready = QSignalSpy(worker.result_ready)

    worker.start()

    assert worker.wait(5000)
    app.processEvents()
    assert not worker.isRunning()
    assert native_finished.count() == 1
    assert result_ready.count() == 1


def test_geo_worker_copies_orm_values_before_session_expires(monkeypatch) -> None:
    class ExpiringRow:
        attached = True
        values = {
            "id": 41,
            "cadastral_number": "76:23:010101:41",
            "address": "Ярославль, Советская площадь, 1",
            "title": "Нежилое помещение",
            "description": "",
            "region_name": "Ярославская область",
            "source_system": "test",
            "source_url": None,
            "lot_url": None,
        }

        def __getattr__(self, name):
            if not self.attached:
                raise RuntimeError("detached ORM row was accessed")
            return self.values[name]

    row = ExpiringRow()
    scope_number = 0

    class Result:
        def __init__(self, values):
            self.values = values

        def all(self):
            return self.values

    class FakeSession:
        def __init__(self, number):
            self.number = number

        def scalars(self, _statement):
            return Result([41] if self.number == 1 else [row])

        @staticmethod
        def get(_model, _lot_id):
            return None

    @contextmanager
    def expiring_session_scope():
        nonlocal scope_number
        scope_number += 1
        try:
            yield FakeSession(scope_number)
        finally:
            if scope_number == 2:
                row.attached = False

    monkeypatch.setattr("bankrotai.gui.session_scope", expiring_session_scope)
    monkeypatch.setattr("bankrotai.gui.NormalizedLot.from_processed_lot", lambda _row: object())
    monkeypatch.setattr("bankrotai.gui.is_sale_real_estate_lot", lambda _lot: True)
    monkeypatch.setattr("bankrotai.geo.resolve_lot_geo", lambda *args, **kwargs: None)

    app = QCoreApplication.instance() or QCoreApplication([])
    worker = GeoWorker(lot_ids=[41])
    errors = QSignalSpy(worker.error)
    completed = QSignalSpy(worker.finished)

    worker.run()
    app.processEvents()

    assert errors.count() == 0
    assert completed.count() == 1
