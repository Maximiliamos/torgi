from __future__ import annotations

from bankrotai.scrapers import TorgiGovClient, TorgiGovSearchFilters


def test_torgi_params_are_api_compatible():
    client = TorgiGovClient()
    filters = TorgiGovSearchFilters(
        search_text="\u0437\u0435\u043c\u0435\u043b\u044c\u043d\u044b\u0439 \u0443\u0447\u0430\u0441\u0442\u043e\u043a",
        subject_rf="\u041e\u043c\u0441\u043a\u0430\u044f \u043e\u0431\u043b\u0430\u0441\u0442\u044c",
        category_code="2",
        price_min=1000,
        price_max=500000,
        publish_date_from="2026-01-01",
        publish_date_to="2026-01-31",
        page=1,
        page_size=100,
    )
    params, warnings = client._build_query_params(filters)

    assert warnings == []
    assert params["text"] == "\u0437\u0435\u043c\u0435\u043b\u044c\u043d\u044b\u0439 \u0443\u0447\u0430\u0441\u0442\u043e\u043a"
    assert params["dynSubjRF"] == "55"
    assert params["catCode"] == "2"
    assert params["priceMin"] == "1000"
    assert params["priceMax"] == "500000"
    assert params["pubFrom"] == "2026-01-01"
    assert params["pubTo"] == "2026-01-31"
    assert params["page"] == "0"
    assert params["size"] == "100"


def test_torgi_params_for_yaroslavl_real_estate_published_applications():
    client = TorgiGovClient()
    filters = TorgiGovSearchFilters(
        subject_rf="\u042f\u0440\u043e\u0441\u043b\u0430\u0432\u0441\u043a\u0430\u044f \u043e\u0431\u043b\u0430\u0441\u0442\u044c",
        category_code="7",
        notice_status="PUBLISHED",
        lot_status="APPLICATIONS_SUBMISSION",
        page=1,
        page_size=100,
    )

    params, warnings = client._build_query_params(filters)

    assert warnings == []
    assert params["dynSubjRF"] == "76"
    assert params["catCode"] == "8,9,10,11,12"
    assert params["noticeStatus"] == "PUBLISHED"
    assert params["lotStatus"] == "APPLICATIONS_SUBMISSION"
    assert params["page"] == "0"
    assert params["size"] == "100"


def test_torgi_params_keep_real_estate_child_category_codes():
    client = TorgiGovClient()
    filters = TorgiGovSearchFilters(category_code="9")

    params, warnings = client._build_query_params(filters)

    assert warnings == []
    assert params["catCode"] == "9"


def test_torgi_params_add_default_active_lot_status():
    client = TorgiGovClient()
    params, _warnings = client._build_query_params(TorgiGovSearchFilters())
    assert params["lotStatus"] == TorgiGovClient.DEFAULT_LOT_STATUS


def test_empty_payload_does_not_crash():
    client = TorgiGovClient()
    items, total, warning = client._extract_items({})
    assert items == []
    assert total is None
    assert warning


def test_normalize_mock_lot():
    client = TorgiGovClient()
    lot = client._normalize_api_lot({
        "id": "123_1",
        "lotName": "\u0417\u0435\u043c\u0435\u043b\u044c\u043d\u044b\u0439 \u0443\u0447\u0430\u0441\u0442\u043e\u043a, \u041e\u043c\u0441\u043a\u0430\u044f \u043e\u0431\u043b\u0430\u0441\u0442\u044c",
        "lotDescription": "\u0417\u0435\u043c\u0435\u043b\u044c\u043d\u044b\u0439 \u0443\u0447\u0430\u0441\u0442\u043e\u043a \u043f\u043b\u043e\u0449\u0430\u0434\u044c\u044e 1000 \u043a\u0432.\u043c, \u043a\u0430\u0434\u0430\u0441\u0442\u0440\u043e\u0432\u044b\u0439 \u043d\u043e\u043c\u0435\u0440 55:36:000000:123",
        "categoryCode": "2",
        "subjectRFName": "\u041e\u043c\u0441\u043a\u0430\u044f \u043e\u0431\u043b\u0430\u0441\u0442\u044c",
        "initialPrice": 100000,
        "lotStatus": "APPLICATIONS_SUBMISSION",
        "firstVersionPublicationDate": "2026-01-01T10:00:00",
    })

    assert lot.external_id == "torgi_gov:123_1"
    assert lot.source == "torgi_gov"
    assert lot.source_system == "torgi.gov.ru"
    assert lot.category == "land"
    assert lot.auction_status == "active"
    assert lot.start_price == 100000
    assert lot.cadastral_number == "55:36:000000:123"
