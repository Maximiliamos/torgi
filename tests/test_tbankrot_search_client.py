from bankrotai.scrapers import ParsedLotData, TBankrotClient, TBankrotSearchFilters
import json


def test_tbankrot_search_params_match_site_form_fields():
    filters = TBankrotSearchFilters(
        search_text="квартира",
        region="999",
        price_min=100000,
        price_max=2500000,
        lot_number="7523707",
        trade_type="public",
        photo_only=True,
        debtor="Иванов",
        auction_manager="Петров",
        organizer="Организатор",
        stop_words="доля",
        show_closed=True,
        show_paused=True,
        page=3,
    )

    params = TBankrotClient()._build_query_params(filters)

    assert ("p", "search") in params
    assert ("search", "квартира") in params
    assert ("region[]", "999") in params
    assert ("start_p1", "100000") in params
    assert ("start_p2", "2500000") in params
    assert ("num", "7523707") in params
    assert ("parent_cat", "2") in params
    assert ("sub_cat", "3,4,5") in params
    assert ("type_1", "on") in params
    assert ("type_2", "on") not in params
    assert ("photo", "1") in params
    assert ("debtor", "Иванов") in params
    assert ("au", "Петров") in params
    assert ("org", "Организатор") in params
    assert ("stop", "доля") in params
    assert ("show_closed", "1") in params
    assert ("show_paused", "1") in params
    assert ("page", "3") in params


def test_tbankrot_first_page_does_not_force_sort_or_page():
    params = TBankrotClient()._build_query_params(TBankrotSearchFilters())

    assert ("p", "search") in params
    assert not any(name == "sort" for name, _value in params)
    assert not any(name == "sort_order" for name, _value in params)
    assert not any(name == "page" for name, _value in params)


def test_tbankrot_can_select_one_real_estate_category():
    params = TBankrotClient()._build_query_params(
        TBankrotSearchFilters(category_codes="5")
    )

    assert ("parent_cat", "2") in params
    assert ("sub_cat", "5") in params


def test_tbankrot_regional_page_uses_slug_url_and_page_size_cookie():
    filters = TBankrotSearchFilters(region="1", page=1, page_size=100)
    client = TBankrotClient()

    params = client._build_query_params(filters)
    url = client._prepare_url(params, filters)
    client._set_page_item_count(filters.page_size)

    assert url.startswith("https://tbankrot.ru/torgi/r/respublika-adygeya?")
    assert ("p", "search") not in params
    assert ("region[]", "1") not in params
    assert ("sort", "created") in params
    assert ("sort_order", "desc") in params
    assert ("show_period", "all") in params
    assert ("page", "1") in params
    assert client.session.cookies.get("pageitemcount") == "100"


def test_tbankrot_regional_path_is_resolved_for_all_region_labels():
    client = TBankrotClient()

    assert client._regional_path("70") == "tulskaya-oblast"
    assert client._regional_path("87") == "respublika-tyva"
    assert client._regional_path("76") == "hanty-mansiyskiy-ao"


def test_tbankrot_tula_search_uses_regional_url_not_legacy_region_param():
    filters = TBankrotSearchFilters(region="70", page=1, page_size=100)
    client = TBankrotClient()

    params = client._build_query_params(filters)
    url = client._prepare_url(params, filters)

    assert url.startswith("https://tbankrot.ru/torgi/r/tulskaya-oblast?")
    assert ("p", "search") not in params
    assert ("region[]", "70") not in params
    assert ("show_period", "all") in params


def test_tbankrot_region_slug_uses_official_cadastral_region_code():
    client = TBankrotClient()

    assert client._official_region_code("Ярославская область") == "76"
    assert client._official_region_code("Тульская область") == "71"
    assert client._official_region_code("Ярославская область", "76:14:030102:303") == "76"


def test_tbankrot_region_filter_accepts_official_code_name_and_slug():
    assert TBankrotClient.normalize_region_filter("76") == "84"
    assert TBankrotClient.normalize_region_filter("Ярославская область") == "84"
    assert TBankrotClient.normalize_region_filter("yaroslavl") == "84"
    assert TBankrotClient.normalize_region_filter("84") == "84"


def test_tbankrot_normalized_lot_keeps_site_region_code_in_raw_data():
    item = ParsedLotData(
        external_id="1",
        title="Земельный участок",
        url="https://tbankrot.ru/item?id=1",
        cadastral_number="76:14:030102:303",
    )

    lot = TBankrotClient()._normalize_parsed_lot(
        item,
        filters=TBankrotSearchFilters(region="84"),
        raw_endpoint="https://tbankrot.ru/torgi/r/yaroslavskaya-oblast",
    )

    assert lot.region_slug == "76"
    assert lot.region_name == "Ярославская область"
    assert lot.raw_data["tbankrot_region_code"] == "84"
    assert lot.raw_data["region_code"] == "76"


def test_tbankrot_search_all_uses_total_pages_even_when_page_size_cookie_is_ignored():
    class FakeClient(TBankrotClient):
        def search_filtered_lots(self, filters):
            lots = [
                type("Lot", (), {"external_id": f"lot-{filters.page}-{idx}"})()
                for idx in range(2)
            ]
            return lots, {"total_pages": 3}

    lots, meta = FakeClient().search_all_lots(
        TBankrotSearchFilters(region="70", page_size=100),
        max_items=None,
    )

    assert len(lots) == 6
    assert meta["pages_loaded"] == 3
    assert meta["stop_reason"] == "last_page"


def test_tbankrot_listing_html_normalizes_to_lot():
    html = """
    <div class="lot_container">
      <div class="lot" data-id="7523707"></div>
      <p class="lot_title"><a href="/item?id=7523707">Toyota Camry 2021 г.</a></p>
      <a class="lot_num">Лот 7523707</a>
      <div class="lot_description"><div class="text">Адрес: г. Ярославль, ул. Ленина, 1</div></div>
      <div class="lot_prices"><div class="current_price"><span>1 824 000,00 ₽</span></div></div>
      <div class="inline_dates"><div class="date">Идут торги</div><div class="date">Осталось 5 дней</div></div>
    </div>
    """

    client = TBankrotClient()
    lots = client._parse_listing_html(
        html,
        filters=TBankrotSearchFilters(region="84"),
        raw_endpoint="https://tbankrot.ru/?p=search&region%5B%5D=84",
    )

    assert len(lots) == 1
    lot = lots[0]
    assert lot.external_id == "tbankrot:7523707"
    assert lot.source == "tbankrot"
    assert lot.source_system == "tbankrot.ru"
    assert lot.region_name == "Ярославская область"
    assert lot.current_price == 1824000.0
    assert lot.lot_url == "https://tbankrot.ru/item?id=7523707"
    assert lot.raw_data["raw_endpoint"].startswith("https://tbankrot.ru/")


def test_tbankrot_regional_card_without_title_link_is_parsed():
    html = """
    <div class="lot_container">
      <div class="lot" data-id="7755288">
        <div class="lot_title">
          <a class="lot_num" href="/item?id=7755288">7755288</a>
          <a class="link_new_tab" href="/item?id=7755288"></a>
          <span class="lot_number">Лот 1</span>
        </div>
        <div class="lot_description"><div class="text">Легковой автомобиль LADA Vesta 2023 г.</div></div>
        <div class="lot_prices"><div class="current_price"><span>810 000,00 ₽</span></div></div>
      </div>
    </div>
    """

    lots = TBankrotClient()._parse_listing_html(
        html,
        filters=TBankrotSearchFilters(region="1"),
        raw_endpoint="https://tbankrot.ru/torgi/r/respublika-adygeya",
    )

    assert len(lots) == 1
    assert lots[0].external_id == "tbankrot:7755288"
    assert lots[0].title.startswith("Легковой автомобиль")
    assert lots[0].current_price == 810000.0


def test_tbankrot_online_search_keeps_only_real_estate(monkeypatch):
    html = """
    <div class="lot_container">
      <div class="lot" data-id="100">
      </div>
      <p class="lot_title"><a href="/item?id=100">Легковой автомобиль</a></p>
      <div class="lot_description"><div class="text">Автомобиль LADA Vesta</div></div>
    </div>
    <div class="lot_container">
      <div class="lot" data-id="200">
      </div>
      <p class="lot_title"><a href="/item?id=200">Нежилое здание</a></p>
      <div class="lot_description"><div class="text">Здание, кадастровый номер 76:01:000001:1</div></div>
    </div>
    """

    class Response:
        url = "https://tbankrot.ru/?p=search"
        text = html

        @staticmethod
        def raise_for_status():
            return None

    client = TBankrotClient()
    monkeypatch.setattr(client.session, "get", lambda *args, **kwargs: Response())

    lots, meta = client.search_filtered_lots(TBankrotSearchFilters())

    assert [lot.external_id for lot in lots] == ["tbankrot:100", "tbankrot:200"]
    assert meta["loaded"] == 2
    assert meta["warnings"]
    assert lots[0].raw_data["passes_investment_real_estate_filter"] is False
    assert lots[1].raw_data["passes_investment_real_estate_filter"] is True


def test_tbankrot_extracts_site_result_total() -> None:
    html = """
    <div class="search_result_col"><div><span>Найдено лотов:</span><b class="default">641</b></div></div>
    <span class="gray_upper">641 аукционов</span>
    """

    assert TBankrotClient()._extract_search_total(html) == 641


def test_tbankrot_rejects_login_limited_nationwide_listing(monkeypatch) -> None:
    html = """
    <div class="search_result_col not_auth">
      <span>Найдено лотов:</span><b class="default">54 616</b>
    </div>
    <div class="blockModal"><p>Для просмотра лотов зарегистрируйтесь или войдите</p></div>
    <div class="lot_list_container blur">
      <div class="lot_container"><div class="lot" data-id="7962479"></div></div>
    </div>
    """

    class Response:
        url = "https://tbankrot.ru/?p=search&parent_cat=2&sub_cat=3%2C4%2C5"
        text = html

        @staticmethod
        def raise_for_status():
            return None

    client = TBankrotClient()
    monkeypatch.setattr(client.session, "get", lambda *args, **kwargs: Response())

    try:
        client.search_filtered_lots(TBankrotSearchFilters())
    except RuntimeError as exc:
        assert "access_limited" in str(exc)
        assert "54616" in str(exc)
        assert "only 1 cards" in str(exc)
    else:
        raise AssertionError("access-limited listing must not be treated as a complete page")


def test_tbankrot_loads_only_tbankrot_session_cookies(tmp_path) -> None:
    cookie_file = tmp_path / "cookies.json"
    cookie_file.write_text(json.dumps({"cookies": [
        {"name": "session", "value": "secret", "domain": ".tbankrot.ru", "path": "/"},
        {"name": "foreign", "value": "ignore", "domain": ".example.com", "path": "/"},
    ]}), encoding="utf-8")

    client = TBankrotClient(cookie_file=str(cookie_file))

    assert client.session.cookies.get("session", domain=".tbankrot.ru") == "secret"
    assert client.session.cookies.get("foreign") is None
