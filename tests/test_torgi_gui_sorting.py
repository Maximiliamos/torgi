from __future__ import annotations

from datetime import datetime

from bankrotai.gui import (
    MAP_ICON_FILENAMES,
    SORT_ROLE,
    MainWindow,
    make_date_item,
    make_number_item,
    make_text_item,
)


def test_number_items_sort_by_numeric_value():
    low = make_number_item(97050)
    high = make_number_item(305982400)

    assert low.data(SORT_ROLE) == 97050
    assert high.data(SORT_ROLE) == 305982400
    assert low < high


def test_date_items_sort_by_datetime_value():
    early = make_date_item("2026-01-01T10:00:00")
    late = make_date_item("2026-02-01")

    assert early.data(SORT_ROLE) == datetime(2026, 1, 1, 10, 0, 0)
    assert late.data(SORT_ROLE) == datetime(2026, 2, 1)
    assert early < late


def test_text_items_sort_case_insensitively():
    first = make_text_item("abc")
    second = make_text_item("Zed")

    assert first.data(SORT_ROLE) == "abc"
    assert second.data(SORT_ROLE) == "zed"
    assert first < second


def test_map_icons_are_embedded_as_data_urls():
    urls = MainWindow.get_map_icon_urls(object())

    assert set(urls) == set(MAP_ICON_FILENAMES)
    assert all(value.startswith("data:image/png;base64,") for value in urls.values())
