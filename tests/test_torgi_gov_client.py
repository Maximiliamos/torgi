from __future__ import annotations

from bankrotai.scrapers import TorgiGovClient, TorgiGovSearchFilters


LAND_SEARCH = "\u0437\u0435\u043c\u0435\u043b\u044c\u043d\u044b\u0439 \u0443\u0447\u0430\u0441\u0442\u043e\u043a"
OMSK_REGION = "\u041e\u043c\u0441\u043a\u0430\u044f \u043e\u0431\u043b\u0430\u0441\u0442\u044c"


def test_torgi_gov_normalizes_mock_lot(monkeypatch) -> None:
    client = TorgiGovClient()
    payload = {
        "content": [
            {
                "id": "24000002380000000612_1",
                "noticeNumber": "24000002380000000612",
                "lotName": f"{LAND_SEARCH} 55:36:000000:123",
                "lotDescription": f"{OMSK_REGION}, \u0433\u043e\u0440\u043e\u0434 \u041e\u043c\u0441\u043a, 1487 \u043a\u0432.\u043c",
                "categoryCode": "2",
                "subjectRFName": OMSK_REGION,
                "priceMin": 1250000,
                "lotStatus": "APPLICATIONS_SUBMISSION",
                "firstVersionPublicationDate": "2026-05-01T10:00:00",
            }
        ],
        "totalElements": 1,
        "last": True,
    }
    monkeypatch.setattr(client, "_request_json", lambda _params: payload)

    lots, meta = client.search_lots(TorgiGovSearchFilters(search_text=LAND_SEARCH))

    assert meta["total"] == 1
    assert lots[0].external_id == "torgi_gov:24000002380000000612_1"
    assert lots[0].source == "torgi_gov"
    assert lots[0].source_system == "torgi.gov.ru"
    assert lots[0].category == "land"
    assert lots[0].region_name == OMSK_REGION
    assert lots[0].cadastral_number == "55:36:000000:123"
    assert lots[0].start_price == 1250000
    assert lots[0].auction_status == "active"


def test_torgi_gov_empty_response_does_not_fail(monkeypatch) -> None:
    client = TorgiGovClient()
    monkeypatch.setattr(client, "_request_json", lambda _params: {"content": [], "totalElements": 0, "last": True})

    lots, meta = client.search_lots(TorgiGovSearchFilters())

    assert lots == []
    assert meta["total"] == 0
    assert meta["has_more"] is False


def test_torgi_gov_filters_are_converted_to_query_params() -> None:
    client = TorgiGovClient()
    params, warnings = client._build_query_params(
        TorgiGovSearchFilters(
            search_text=LAND_SEARCH,
            subject_rf=OMSK_REGION,
            category_code="2",
            price_min=1000,
            price_max=5000,
            notice_number="2200001",
            organizer_inn="5500000000",
            publish_date_from="2026-05-01",
            publish_date_to="2026-05-08",
            page=1,
            page_size=25,
        )
    )

    assert warnings == []
    assert params["text"] == LAND_SEARCH
    assert params["dynSubjRF"] == "55"
    assert params["catCode"] == "2"
    assert params["typeTransaction"] == "SALE"
    assert params["priceMin"] == "1000"
    assert params["priceMax"] == "5000"
    assert params["noticeNumber"] == "2200001"
    assert params["organizerInn"] == "5500000000"
    assert params["pubFrom"] == "2026-05-01"
    assert params["pubTo"] == "2026-05-08"
    assert params["page"] == "0"
    assert params["size"] == "25"


def test_torgi_gov_plain_location_is_not_sent_as_fias() -> None:
    client = TorgiGovClient()

    params, warnings = client._build_query_params(TorgiGovSearchFilters(fias="\u041c\u043e\u0441\u043a\u0432\u0430"))

    assert "fias" not in params
    assert any("free-text" in warning for warning in warnings)


def test_torgi_gov_fias_guid_is_sent_to_query() -> None:
    client = TorgiGovClient()
    guid = "01234567-89ab-cdef-0123-456789abcdef"

    params, warnings = client._build_query_params(TorgiGovSearchFilters(fias=guid))

    assert warnings == []
    assert params["fias"] == guid


def test_torgi_gov_filters_plain_location_locally(monkeypatch) -> None:
    client = TorgiGovClient()
    payload = {
        "content": [
            {
                "id": "tula_1",
                "lotName": "\u041d\u0435\u0436\u0438\u043b\u043e\u0435 \u043f\u043e\u043c\u0435\u0449\u0435\u043d\u0438\u0435",
                "lotDescription": "\u0422\u0443\u043b\u044c\u0441\u043a\u0430\u044f \u043e\u0431\u043b\u0430\u0441\u0442\u044c",
                "categoryCode": "10",
                "subjectRFName": "\u0422\u0443\u043b\u044c\u0441\u043a\u0430\u044f \u043e\u0431\u043b\u0430\u0441\u0442\u044c",
                "lotStatus": "PUBLISHED",
            },
            {
                "id": "moscow_1",
                "lotName": "\u041f\u043e\u043c\u0435\u0449\u0435\u043d\u0438\u0435",
                "lotDescription": "\u0433. \u041c\u043e\u0441\u043a\u0432\u0430, \u0443\u043b\u0438\u0446\u0430 \u0422\u0435\u0441\u0442\u043e\u0432\u0430\u044f",
                "categoryCode": "10",
                "subjectRFName": "\u041c\u043e\u0441\u043a\u0432\u0430",
                "address": "\u0433. \u041c\u043e\u0441\u043a\u0432\u0430",
                "lotStatus": "PUBLISHED",
            },
        ],
        "totalElements": 2,
        "last": True,
    }
    monkeypatch.setattr(client, "_request_json", lambda _params: payload)

    lots, meta = client.search_lots(TorgiGovSearchFilters(fias="\u041c\u043e\u0441\u043a\u0432\u0430"))

    assert [lot.external_id for lot in lots] == ["torgi_gov:moscow_1"]
    assert meta["location_filtered"] == 1
    assert "fias" not in meta["raw_params"]


def test_torgi_gov_location_filter_does_not_match_adjective_region(monkeypatch) -> None:
    client = TorgiGovClient()
    payload = {
        "content": [
            {
                "id": "moscow_region_1",
                "lotName": "\u0421\u043a\u043b\u0430\u0434",
                "lotDescription": "\u041c\u043e\u0441\u043a\u043e\u0432\u0441\u043a\u0430\u044f \u043e\u0431\u043b\u0430\u0441\u0442\u044c",
                "categoryCode": "10",
                "subjectRFName": "\u041c\u043e\u0441\u043a\u043e\u0432\u0441\u043a\u0430\u044f \u043e\u0431\u043b\u0430\u0441\u0442\u044c",
                "lotStatus": "PUBLISHED",
            },
        ],
        "totalElements": 1,
        "last": True,
    }
    monkeypatch.setattr(client, "_request_json", lambda _params: payload)

    lots, meta = client.search_lots(TorgiGovSearchFilters(fias="\u041c\u043e\u0441\u043a\u0432\u0430"))

    assert lots == []
    assert meta["location_filtered"] == 1


def test_torgi_gov_city_search_text_is_strictly_checked_locally(monkeypatch) -> None:
    client = TorgiGovClient()
    payload = {
        "content": [
            {
                "id": "not_yaroslavl_1",
                "lotName": "\u0417\u0434\u0430\u043d\u0438\u0435",
                "lotDescription": "\u041a\u0440\u0430\u0441\u043d\u043e\u0434\u0430\u0440\u0441\u043a\u0438\u0439 \u043a\u0440\u0430\u0439",
                "categoryCode": "10",
                "subjectRFName": "\u041a\u0440\u0430\u0441\u043d\u043e\u0434\u0430\u0440\u0441\u043a\u0438\u0439 \u043a\u0440\u0430\u0439",
                "lotStatus": "PUBLISHED",
            },
            {
                "id": "yaroslavl_1",
                "lotName": "\u041f\u043e\u043c\u0435\u0449\u0435\u043d\u0438\u0435",
                "lotDescription": "\u0433. \u042f\u0440\u043e\u0441\u043b\u0430\u0432\u043b\u044c",
                "categoryCode": "10",
                "subjectRFName": "\u042f\u0440\u043e\u0441\u043b\u0430\u0432\u0441\u043a\u0430\u044f \u043e\u0431\u043b\u0430\u0441\u0442\u044c",
                "lotStatus": "PUBLISHED",
            },
        ],
        "totalElements": 2,
        "last": True,
    }
    monkeypatch.setattr(client, "_request_json", lambda _params: payload)

    lots, meta = client.search_lots(TorgiGovSearchFilters(search_text="\u042f\u0440\u043e\u0441\u043b\u0430\u0432\u043b\u044c"))

    assert [lot.external_id for lot in lots] == ["torgi_gov:yaroslavl_1"]
    assert meta["text_filtered"] == 1
    assert meta["raw_params"]["text"] == "\u042f\u0440\u043e\u0441\u043b\u0430\u0432\u043b\u044c"


def test_torgi_gov_subject_filter_hides_other_regions(monkeypatch) -> None:
    client = TorgiGovClient()
    payload = {
        "content": [
            {
                "id": "omsk_1",
                "lotName": "Земельный участок 55:36:000000:123",
                "lotDescription": "Омская область, город Омск",
                "categoryCode": "2",
                "subjectRFName": OMSK_REGION,
                "lotStatus": "PUBLISHED",
            },
            {
                "id": "moscow_1",
                "lotName": "Земельный участок 50:01:000000:123",
                "lotDescription": "Московская область",
                "categoryCode": "2",
                "subjectRFName": "Московская область",
                "lotStatus": "PUBLISHED",
            },
        ],
        "totalElements": 2,
        "last": True,
    }
    monkeypatch.setattr(client, "_request_json", lambda _params: payload)

    lots, meta = client.search_lots(TorgiGovSearchFilters(subject_rf=OMSK_REGION))

    assert [lot.external_id for lot in lots] == ["torgi_gov:omsk_1"]
    assert meta["region_filtered"] == 1


def test_torgi_gov_deduplicates_external_id(monkeypatch) -> None:
    client = TorgiGovClient()
    item = {
        "id": "duplicate_1",
        "lotName": "\u041d\u0435\u0436\u0438\u043b\u043e\u0435 \u043f\u043e\u043c\u0435\u0449\u0435\u043d\u0438\u0435",
        "categoryCode": "7",
        "priceMin": 10,
        "lotStatus": "PUBLISHED",
    }
    monkeypatch.setattr(client, "_request_json", lambda _params: {"content": [item, dict(item)], "totalElements": 2})

    lots, meta = client.search_lots(TorgiGovSearchFilters())

    assert len(lots) == 1
    assert lots[0].external_id == "torgi_gov:duplicate_1"
    assert meta["total"] == 2


def test_torgi_gov_search_all_lots_loads_pages_and_deduplicates(monkeypatch) -> None:
    client = TorgiGovClient()
    payloads = {
        0: {
            "content": [
                {"id": "lot_1", "lotName": "Lot 1", "categoryCode": "10", "lotStatus": "PUBLISHED"},
                {"id": "lot_2", "lotName": "Lot 2", "categoryCode": "10", "lotStatus": "PUBLISHED"},
            ],
            "totalElements": 3,
            "last": False,
        },
        1: {
            "content": [
                {"id": "lot_2", "lotName": "Lot 2 duplicate", "categoryCode": "10", "lotStatus": "PUBLISHED"},
                {"id": "lot_3", "lotName": "Lot 3", "categoryCode": "10", "lotStatus": "PUBLISHED"},
            ],
            "totalElements": 3,
            "last": True,
        },
    }

    def fake_request(params):
        return payloads[int(params["page"])]

    monkeypatch.setattr(client, "_request_json", fake_request)

    lots, meta = client.search_all_lots(TorgiGovSearchFilters(page_size=2), max_items=10)

    assert [lot.external_id for lot in lots] == ["torgi_gov:lot_1", "torgi_gov:lot_2", "torgi_gov:lot_3"]
    assert meta["mode"] == "all_pages"
    assert meta["loaded"] == 3
    assert meta["total"] == 3


def test_torgi_gov_search_all_lots_does_not_stop_on_duplicate_only_page(monkeypatch) -> None:
    client = TorgiGovClient()
    requested_pages: list[int] = []
    payloads = {
        0: {
            "content": [{"id": "lot_1", "lotName": "Lot 1", "categoryCode": "10", "lotStatus": "PUBLISHED"}],
            "totalElements": 2,
            "totalPages": 3,
            "last": False,
        },
        1: {
            "content": [{"id": "lot_1", "lotName": "Lot 1 duplicate", "categoryCode": "10", "lotStatus": "PUBLISHED"}],
            "totalElements": 2,
            "totalPages": 3,
            "last": False,
        },
        2: {
            "content": [{"id": "lot_2", "lotName": "Lot 2", "categoryCode": "10", "lotStatus": "PUBLISHED"}],
            "totalElements": 2,
            "totalPages": 3,
            "last": True,
        },
    }

    def fake_request(params):
        page = int(params["page"])
        requested_pages.append(page)
        return payloads[page]

    monkeypatch.setattr(client, "_request_json", fake_request)

    lots, meta = client.search_all_lots(TorgiGovSearchFilters(page_size=1), max_items=10)

    assert requested_pages == [0, 1, 2]
    assert [lot.external_id for lot in lots] == ["torgi_gov:lot_1", "torgi_gov:lot_2"]
    assert meta["duplicates"] == 1
    assert meta["pages_loaded"] == 3
    assert meta["stop_reason"] == "reached_total_pages"
    assert meta["raw_items_loaded"] == 3
    assert [page["new_unique"] for page in meta["page_diagnostics"]] == [1, 0, 1]


def test_torgi_gov_search_all_lots_reports_skipped_without_id(monkeypatch) -> None:
    client = TorgiGovClient()
    payload = {
        "content": [
            {"lotName": "No stable id", "categoryCode": "10", "lotStatus": "PUBLISHED"},
            {"id": "lot_1", "lotName": "Lot 1", "categoryCode": "10", "lotStatus": "PUBLISHED"},
        ],
        "totalElements": 2,
        "totalPages": 1,
        "last": True,
    }
    monkeypatch.setattr(client, "_request_json", lambda _params: payload)

    lots, meta = client.search_all_lots(TorgiGovSearchFilters(page_size=100), max_items=10)

    assert [lot.external_id for lot in lots] == ["torgi_gov:lot_1"]
    assert meta["skipped_without_id"] == 1
    assert any("пропущено" in warning for warning in meta["warnings"])


def test_torgi_gov_search_all_lots_reports_page_error(monkeypatch) -> None:
    client = TorgiGovClient()

    def fake_request(params):
        if int(params["page"]) == 1:
            raise RuntimeError("temporary failure")
        return {
            "content": [{"id": "lot_1", "lotName": "Lot 1", "categoryCode": "10", "lotStatus": "PUBLISHED"}],
            "totalElements": 2,
            "totalPages": 2,
            "last": False,
        }

    monkeypatch.setattr(client, "_request_json", fake_request)

    lots, meta = client.search_all_lots(TorgiGovSearchFilters(page_size=1), max_items=10)

    assert [lot.external_id for lot in lots] == ["torgi_gov:lot_1"]
    assert meta["pages_loaded"] == 2
    assert meta["stop_reason"] == "page_2_error"
    assert any("Страница 2 вернула ошибку" in warning for warning in meta["warnings"])
