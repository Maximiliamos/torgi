from __future__ import annotations

import pytest

from bankrotai.connectors.etp_sources.etpu import (
    EtpuClient,
    EtpuSchemaError,
    EtpuStatusFilter,
    SEARCH_BUTTON_FIELD,
    STATUS_FILTER_FIELD,
    classify_property,
    classifier_flags,
    parse_catalogue_page,
    parse_detail,
)


def _detail(
    *,
    classifier: str,
    title: str = "Земельный участок с нежилым зданием",
    start_price: str = "1 234 567,89",
    current_price: str | None = None,
    status: str = "Прием заявок",
) -> str:
    current = f"Текущая цена, руб.: {current_price} " if current_price is not None else ""
    return f"""
    <html><body>
      <h1>Информация об аукционе №0055237</h1>
      <section>
        Информация о лоте № 1
        Наименование: {title}
        Статус: {status}
        Дата начала представления заявок (Московское время): 09.11.2026 10:00:00
        Дата окончания представления заявок на участие (Московское время): 30.11.2026 10:00:00
        Дата проведения (Московское время): 04.12.2026 12:00:00
        Начальная цена, руб.: {start_price}
        {current}
        Размер задатка, руб.: 246 913,58
        Шаг аукциона, руб.: 61 728,39
        Классификатор ЕФРСБ: {classifier}
        Документы
      </section>
    </body></html>
    """


def test_etpu_detail_uses_individual_labelled_price_status_and_classifier() -> None:
    parsed = parse_detail(
        _detail(classifier="Земельные участки; Здания (помещения) жилые, не входящие в жилищный фонд"),
        detail_url="https://bankrupt.etpu.ru/public/auctions/lots/view/143436/",
    )

    assert parsed.external_id == "etpu.ru:auctions-143436"
    assert parsed.start_price == 1_234_567.89
    assert parsed.current_price is None
    assert parsed.deposit_amount == 246_913.58
    assert parsed.auction_step_amount == 61_728.39
    assert parsed.auction_status == "active"
    assert parsed.procedure_number == "0055237"
    assert parsed.category == "real_estate"
    assert parsed.raw_data["efrsb_classifier_flags"] == ["land", "real_estate"]
    assert parsed.raw_data["structured_property_classification"] is True
    assert parsed.raw_data["detail_initial_price_verified"] is True
    assert parsed.application_start_at.isoformat() == "2026-11-09T10:00:00"
    assert parsed.application_deadline.isoformat() == "2026-11-30T10:00:00"
    assert parsed.auction_at.isoformat() == "2026-12-04T12:00:00"
    assert parsed.auction_timezone == "Europe/Moscow"


def test_etpu_property_fields_are_bound_to_the_individual_detail() -> None:
    html = _detail(
        classifier="Здания (кроме жилых) и сооружения, не включенные в другие группировки",
        title=(
            "Нежилое помещение площадью 48,6 кв.м., кадастровый № "
            "78:12:0007203:5171, расположенное по адресу: "
            "Санкт-Петербург, Рыбацкий пр., д. 18, литера А;"
        ),
    )
    parsed = parse_detail(
        html,
        detail_url="https://bankrupt.etpu.ru/public/auctions/lots/view/141915/",
    )

    assert parsed.cadastral_number == "78:12:0007203:5171"
    assert parsed.area == 48.6
    assert parsed.address == "Санкт-Петербург, Рыбацкий пр., д. 18, литера А"


def test_etpu_public_offer_keeps_initial_and_current_prices_distinct() -> None:
    parsed = parse_detail(
        _detail(
            classifier="Права требования к физическим лицам",
            title="Право требования по договору займа",
            start_price="900 000,00",
            current_price="540 000,00",
        ),
        detail_url="https://bankrupt.etpu.ru/public/public-offers/lots/view/143438/",
    )

    assert parsed.external_id == "etpu.ru:public-offers-143438"
    assert parsed.start_price == 900_000
    assert parsed.current_price == 540_000
    assert parsed.category == "receivables"
    assert parsed.auction_type == "public_offer"


def test_etpu_public_offer_uses_last_interval_as_application_deadline() -> None:
    html = _detail(
        classifier="Права требования к физическим лицам",
        title="Право требования",
        start_price="503 500,00",
        current_price="503 500,00",
    ).replace(
        "Дата окончания представления заявок на участие (Московское время): 30.11.2026 10:00:00",
        "Дата окончания последнего интервала (Московское время): 30.11.2026 14:00:00",
    ).replace(
        "Дата проведения (Московское время): 04.12.2026 12:00:00",
        "Дата начала первого интервала (Московское время): 09.11.2026 14:00:00",
    )
    parsed = parse_detail(
        html,
        detail_url="https://bankrupt.etpu.ru/public/public-offers/lots/view/143362/",
    )

    assert parsed.application_deadline.isoformat() == "2026-11-30T14:00:00"
    assert parsed.auction_at.isoformat() == "2026-11-09T14:00:00"


def test_etpu_public_offer_interval_schedule_stays_separate_from_prices() -> None:
    html = _detail(
        classifier="Права требования к физическим лицам",
        start_price="663 187,00",
        current_price="66 318,70",
    ).replace(
        "</section>",
        """
        <table>
          <tr>
            <th>Дата начала интервала</th>
            <th>Дата окончания интервала</th>
            <th>Задаток на интервале, руб.</th>
            <th>Цена на интервале, руб.</th>
          </tr>
          <tr>
            <td>24.08.2026 11:00</td><td>31.08.2026 11:00</td>
            <td>66 318,70</td><td>663 187,00</td>
          </tr>
          <tr>
            <td>26.10.2026 11:00</td><td>30.10.2026 17:00</td>
            <td>6 631,87</td><td>66 318,70</td>
          </tr>
        </table>
        </section>
        """,
    )
    parsed = parse_detail(
        html,
        detail_url="https://bankrupt.etpu.ru/public/public-offers/lots/view/138121/",
    )

    assert parsed.start_price == 663_187
    assert parsed.current_price == 66_318.70
    assert parsed.public_offer_schedule is not None
    assert len(parsed.public_offer_schedule) == 2
    assert parsed.public_offer_schedule[-1]["Цена на интервале, руб."] == "66 318,70"


def test_etpu_does_not_invent_price_from_deposit_or_interval_amount() -> None:
    html = """
    <html><body>
      Информация о публичном предложении №0056000
      Информация о лоте № 1
      Наименование: Земельный участок
      Статус: Прием заявок
      Дата окончания представления заявок (Московское время): 30.11.2026 10:00:00
      Размер задатка, руб.: 100 000,00
      Цена на интервале, руб.: 750 000,00
      Классификатор ЕФРСБ: Земельные участки
      Интервалы снижения цены
    </body></html>
    """
    parsed = parse_detail(
        html,
        detail_url="https://bankrupt.etpu.ru/public/public-offers/lots/view/143422/",
    )

    assert parsed.start_price is None
    assert parsed.current_price is None
    assert parsed.deposit_amount == 100_000


@pytest.mark.parametrize(
    ("classifier", "expected"),
    [
        ("Здания (кроме жилых) и сооружения, не включенные в другие группировки", "real_estate"),
        ("Земельные участки", "land"),
        ("Права долевой собственности", "shares"),
        ("Права аренды земельного участка", "lease"),
        ("Средства транспортные прочие, не включенные в другие группировки", "vehicle"),
        ("Производственное, промышленное оборудование", "equipment"),
        ("Права требования к физическим лицам", "receivables"),
    ],
)
def test_etpu_structured_efrsb_categories(classifier: str, expected: str) -> None:
    text = f"Информация о лоте № 1 Классификатор ЕФРСБ: {classifier} Документы"
    assert classify_property(text) == expected
    assert expected in classifier_flags(text)


def test_etpu_unknown_structured_classifier_fails_closed_to_other() -> None:
    parsed = parse_detail(
        _detail(
            classifier="Ценные бумаги и иные финансовые инструменты",
            title="Автомобиль указан только в свободном описании",
        ),
        detail_url="https://bankrupt.etpu.ru/public/auctions/lots/view/143412/",
    )

    assert parsed.category == "other"
    assert parsed.raw_data["efrsb_classifier_flags"] == ["other"]


def test_etpu_official_other_classifier_is_not_overridden_by_free_text() -> None:
    parsed = parse_detail(
        _detail(classifier="Прочее", title="Автомобиль легковой, VIN XTA00000000000000"),
        detail_url="https://bankrupt.etpu.ru/public/auctions/lots/view/143411/",
    )

    assert parsed.category == "other"
    assert parsed.raw_data["efrsb_classifier"] == "Прочее"
    assert parsed.raw_data["efrsb_classifier_flags"] == ["other"]


def test_etpu_status_ignores_status_like_prose_and_unknown_intermediate_phase() -> None:
    missing_label = _detail(
        classifier="Земельные участки",
        status="Нераспознанная стадия",
    ).replace(
        "Статус: Нераспознанная стадия",
        "Порядок участия: прием заявок производится ежедневно",
    )
    parsed_missing = parse_detail(
        missing_label,
        detail_url="https://bankrupt.etpu.ru/public/auctions/lots/view/143420/",
    )
    assert parsed_missing.auction_status == "unknown"

    parsed_interval = parse_detail(
        _detail(
            classifier="Автомобили",
            status="Прием заявок на интервале не активен",
        ),
        detail_url="https://bankrupt.etpu.ru/public/public-offers/lots/view/143395/",
    )
    assert parsed_interval.auction_status == "unknown"
    assert parsed_interval.raw_data["source_status_text"] == (
        "Прием заявок на интервале не активен"
    )


def test_etpu_multi_lot_catalogue_keeps_individual_lot_ids_and_prices() -> None:
    html = """
    <table>
      <tr><th>Торги</th><th>Лот</th><th>Начальная цена</th><th>Статус</th></tr>
      <tr><td>0055237</td>
        <td><a href="/public/auctions/lots/view/143434/">Лот 1. Помещение</a></td>
        <td>2 500 000,00</td><td>Прием заявок</td></tr>
      <tr><td>0055237</td>
        <td><a href="/public/auctions/lots/view/143435/">Лот 2. Земельный участок</a></td>
        <td>900 000,00</td><td>Прием заявок</td></tr>
    </table>
    """
    lots = parse_catalogue_page(html, page=1)

    assert [lot.external_id for lot in lots] == [
        "etpu.ru:auctions-143434",
        "etpu.ru:auctions-143435",
    ]
    assert [lot.start_price for lot in lots] == [2_500_000, 900_000]


def _filter_form(*, selected: str = "", rows: str = "", next_page: int | None = None) -> str:
    options = [
        ("", "Все"),
        ("5", "Извещение опубликовано"),
        ("7", "Прием заявок"),
        ("14", "Прием заявок на интервале не активен"),
        ("8", "Определение участников торгов"),
        ("1", "Идут торги"),
        ("15", "Подведение результатов"),
        ("2", "Окончен"),
        ("3", "Не состоялся"),
        ("4", "Отменён организатором"),
        ("13", "Приостановлен"),
    ]
    rendered = "".join(
        f'<option value="{value}"{" selected" if value == selected else ""}>{text}</option>'
        for value, text in options
    )
    pager = (
        f"<a href=\"javascript:__doPostBack('pager','Page$" + str(next_page) + "')\">"
        + str(next_page)
        + "</a>"
        if next_page is not None
        else ""
    )
    return f"""
    <html><body><form method="post" action="./">
      <input type="hidden" name="__VIEWSTATE" value="state-{selected or 'all'}">
      <input type="text"
        name="ctl00$ctl00$LeftContentLogin$ctl00$Login1$UserName" value="do-not-send">
      <input type="password"
        name="ctl00$ctl00$LeftContentLogin$ctl00$Login1$Password" value="do-not-send">
      <input type="text"
        name="ctl00$ctl00$MainExpandableArea$phExpandCollapse$PurchasesSearchCriteria$vPurchaseLot_lotTitle_Наименованиелота"
        value="">
      <select name="{STATUS_FILTER_FIELD}">{rendered}</select>
      <input type="submit" name="{SEARCH_BUTTON_FIELD}" value="Искать торги">
      {pager}
      <table>
        <tr><th>Лот</th><th>Начальная цена</th><th>Статус</th></tr>
        {rows}
      </table>
    </form></body></html>
    """


def _catalogue_row(lot_id: int, status: str, price: str = "1 000 000,00") -> str:
    return (
        "<tr>"
        f'<td><a href="/public/auctions/lots/view/{lot_id}/">Лот {lot_id}</a></td>'
        f"<td>{price}</td><td>{status}</td>"
        "</tr>"
    )


def test_etpu_official_status_filter_post_excludes_login_and_verifies_rows() -> None:
    initial = _filter_form()
    filtered = _filter_form(
        selected="7",
        rows=_catalogue_row(150001, "Прием заявок"),
    )
    session = _PostbackSession([
        _PagedResponse(initial),
        _PagedResponse(filtered),
    ])
    client = EtpuClient(
        session=session,
        status_filter=EtpuStatusFilter("7", "Прием заявок"),
    )

    page = client.fetch_catalogue_page(1)

    assert [lot.external_id for lot in page.items] == ["etpu.ru:auctions-150001"]
    assert page.items[0].raw_data["source_status_text"] == "Прием заявок"
    assert session.posts[0][STATUS_FILTER_FIELD] == "7"
    assert session.posts[0][SEARCH_BUTTON_FIELD] == "Искать торги"
    assert not any("LeftContentLogin" in name for name in session.posts[0])


def test_etpu_status_filter_persists_through_postback_pagination() -> None:
    initial = _filter_form()
    page_one = _filter_form(
        selected="7",
        rows=_catalogue_row(150001, "Прием заявок"),
        next_page=2,
    )
    page_two = _filter_form(
        selected="7",
        rows=_catalogue_row(150002, "Прием заявок"),
    )
    session = _PostbackSession([
        _PagedResponse(initial),
        _PagedResponse(page_one),
        _PagedResponse(page_two),
    ])
    client = EtpuClient(
        session=session,
        status_filter=EtpuStatusFilter("7", "Прием заявок"),
    )

    first = client.fetch_catalogue_page(1)
    second = client.fetch_catalogue_page(first.next_page)

    assert first.next_page == 2
    assert second.next_page is None
    assert session.posts[1][STATUS_FILTER_FIELD] == "7"
    assert session.posts[1]["__EVENTARGUMENT"] == "Page$2"


def test_etpu_filtered_catalogue_rejects_row_with_different_status() -> None:
    initial = _filter_form()
    filtered = _filter_form(
        selected="7",
        rows=_catalogue_row(150003, "Окончен"),
    )
    session = _PostbackSession([
        _PagedResponse(initial),
        _PagedResponse(filtered),
    ])
    client = EtpuClient(
        session=session,
        status_filter=EtpuStatusFilter("7", "Прием заявок"),
    )

    with pytest.raises(EtpuSchemaError, match="row status does not match requested"):
        client.fetch_catalogue_page(1)


class _PagedResponse:
    status_code = 200

    def __init__(self, body: str, url: str = "https://bankrupt.etpu.ru/public/purchases-all/") -> None:
        self.content = body.encode()
        self.url = url

    def raise_for_status(self) -> None:
        return None


class _PostbackSession:
    def __init__(self, responses: list[_PagedResponse]) -> None:
        self.responses = list(responses)
        self.headers: dict[str, str] = {}
        self.posts: list[dict[str, str]] = []

    def _next(self) -> _PagedResponse:
        return self.responses.pop(0)

    def get(self, *_args, **_kwargs):
        return self._next()

    def post(self, _url, *, data=None, **_kwargs):
        self.posts.append(dict(data or {}))
        return self._next()


def test_etpu_pager_does_not_use_block_jump_for_random_access() -> None:
    page_one = """
    <html><body><form>
      <input type="hidden" name="__VIEWSTATE" value="one">
      <a href="javascript:__doPostBack('pager','Page$10')">10</a>
      <a href="javascript:__doPostBack('pager','Page$Next')">&gt;&gt;</a>
    </form></body></html>
    """
    session = _PostbackSession([_PagedResponse(page_one)])
    client = EtpuClient(session=session)

    with pytest.raises(EtpuSchemaError, match="page 15 postback target disappeared"):
        client.fetch_catalogue_page(15)

    assert session.posts == []


def test_etpu_pager_crosses_ten_page_block_and_finds_real_terminal() -> None:
    page_one = """
    <html><body><form>
      <input type="hidden" name="__VIEWSTATE" value="one">
      <a href="javascript:__doPostBack('pager','Page$10')">10</a>
      <a href="javascript:__doPostBack('pager','Page$Next')">&gt;&gt;</a>
    </form></body></html>
    """
    page_ten = """
    <html><body><form>
      <input type="hidden" name="__VIEWSTATE" value="ten">
      <a href="javascript:__doPostBack('pager','Page$9')">9</a>
      <a href="javascript:__doPostBack('pager','Page$10')">10</a>
      <a href="javascript:__doPostBack('pager','Page$Next')">&gt;&gt;</a>
      <table><tr><td>
        <a href="/public/auctions/lots/view/150010/">Лот страницы 10</a>
      </td></tr></table>
    </form></body></html>
    """
    page_eleven_terminal = """
    <html><body><form>
      <input type="hidden" name="__VIEWSTATE" value="eleven">
      <a href="javascript:__doPostBack('pager','Page$11')">11</a>
      <table><tr><td>
        <a href="/public/auctions/lots/view/150011/">Лот страницы 11</a>
      </td></tr></table>
    </form></body></html>
    """
    session = _PostbackSession([
        _PagedResponse(page_one),
        _PagedResponse(page_ten),
        _PagedResponse(page_eleven_terminal),
    ])
    client = EtpuClient(session=session)

    page10 = client.fetch_catalogue_page(10)
    assert page10.next_page == 11
    assert [item.external_id for item in page10.items] == ["etpu.ru:auctions-150010"]

    page11 = client.fetch_catalogue_page(page10.next_page)
    assert page11.next_page is None
    assert [item.external_id for item in page11.items] == ["etpu.ru:auctions-150011"]

    assert session.posts[0]["__EVENTARGUMENT"] == "Page$10"
    assert session.posts[1]["__EVENTARGUMENT"] == "Page$Next"


class _Response:
    status_code = 200

    def __init__(self, body: str, url: str) -> None:
        self.content = body.encode()
        self.url = url

    def raise_for_status(self) -> None:
        return None


class _Session:
    def __init__(self, body: str) -> None:
        self.body = body
        self.headers: dict[str, str] = {}
        self.requested: list[str] = []

    def get(self, url, *_args, **_kwargs):
        self.requested.append(url)
        return _Response(self.body, url)


def test_etpu_fetch_lot_reconstructs_exact_individual_detail_url() -> None:
    session = _Session(_detail(classifier="Земельные участки"))
    client = EtpuClient(session=session)
    parsed = client.fetch_detail("etpu.ru:auctions-143436")

    assert session.requested == [
        "https://bankrupt.etpu.ru/public/auctions/lots/view/143436/"
    ]
    assert parsed.external_id == "etpu.ru:auctions-143436"


