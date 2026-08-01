from __future__ import annotations

from types import SimpleNamespace

from bankrotai.gui import MainWindow


class _StatusBar:
    def __init__(self) -> None:
        self.messages: list[tuple[str, int]] = []

    def showMessage(self, message: str, timeout: int = 0) -> None:
        self.messages.append((message, timeout))


def _window_stub() -> SimpleNamespace:
    calls: list[object] = []
    return SimpleNamespace(
        calls=calls,
        status_bar=_StatusBar(),
        progress_bar=SimpleNamespace(setVisible=lambda value: calls.append(("progress", value))),
        finish_task_progress=lambda key: calls.append(("finish", key)),
        _set_all_russia_buttons_enabled=lambda value: calls.append(("buttons", value)),
        load_lots=lambda: calls.append("lots"),
        update_map=lambda: calls.append("map"),
        update_yandex_map=lambda: calls.append("yandex"),
        start_geo_worker=lambda **kwargs: calls.append(("geo", kwargs)),
        _sync_map_filter_controls=lambda: calls.append("filters"),
    )


def test_all_russia_completion_refreshes_maps_and_starts_geo() -> None:
    window = _window_stub()

    MainWindow.on_all_russia_search_finished(window, [5, "7"], {"errors": {}})

    assert "lots" in window.calls
    assert "map" in window.calls
    assert "yandex" in window.calls
    assert ("geo", {"lot_ids": [5, 7], "refresh_existing": False, "limit": None}) in window.calls
    assert window.status_bar.messages[-1] == (
        "Поиск РФ завершён: уникальных карточек 2. Запускаю геокодирование...",
        8000,
    )


def test_reset_map_filters_only_resets_filters_and_refreshes_maps() -> None:
    window = _window_stub()
    window.map_filter_min_price = 100
    window.map_filter_max_price = 200
    window.map_filter_region = "Ярославская область"

    MainWindow.reset_map_filters(window, "map")

    assert window.map_filter_min_price == 0.0
    assert window.map_filter_max_price == 0.0
    assert window.map_filter_region == "Все регионы"
    assert window.calls == ["filters", "map", "yandex"]
