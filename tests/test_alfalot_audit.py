from __future__ import annotations

import pytest

from bankrotai.connectors.etp_sources.alfalot_audit import (
    AlfalotContractError,
    ALFALOT_BLOCKED_REASON,
    require_verified_active_http_contract,
    terminal_controls_confirmed,
    verify_gd_filter_state,
    PageProof,
    extract_detail_fields,
    extract_lot_references,
    lot_entity_from_url,
    parse_exact_price,
    property_candidate,
    verify_two_walks,
)


def _page(n: int, ids: tuple[str, ...], last: bool = False) -> PageProof:
    return PageProof(
        page=n, state_id="stateFilter_GD", state_label="прием заявки",
        selected=True, entity_ids=ids, next_page_control=not last,
        terminal_control=last,
    )


def test_unverified_active_http_contract_stays_blocked_inside_alfalot_module() -> None:
    assert "raw HTTP" in ALFALOT_BLOCKED_REASON
    with pytest.raises(AlfalotContractError, match="raw HTTP"):
        require_verified_active_http_contract()


@pytest.mark.parametrize(("label", "selected"), [
    ("прием заявки", False),
    ("все", True),
    ("торги завершены", True),
])
def test_gd_scope_refuses_other_or_unselected_ui_states(label: str, selected: bool) -> None:
    with pytest.raises(AlfalotContractError):
        verify_gd_filter_state(label, selected=selected)


def test_page_terminal_must_have_explicit_disabled_next_and_end_controls() -> None:
    assert terminal_controls_confirmed(["1", "2", "3"], [">", ">>"]) is True
    assert terminal_controls_confirmed(["1", "2", "3"], []) is False
    assert terminal_controls_confirmed(["1", "2", ">"], [">>"]) is False


def test_only_first_party_individual_lotcard_is_accepted() -> None:
    good = "https://etp.alfalot.ru/app/LotCard/page?LotCard.lotEntity=LT%3A0026026025DS.lot1"
    assert lot_entity_from_url(good) == "LT:0026026025DS.lot1"
    # Real ALFALOT official card URLs use opaque per-lot entity IDs.
    actual = "https://etp.alfalot.ru/app/LotCard/page?LotCard.lotEntity=LT%3Acoreboa1bsm6o0000q1pk823cqa7he10"
    assert lot_entity_from_url(actual) == "LT:coreboa1bsm6o0000q1pk823cqa7he10"
    with_session = (
        "https://etp.alfalot.ru/app/LotCard/page;jsessionid=PUBLIC_SESSION_PLACEHOLDER"
        "?LotCard.lotEntity=LF%3Acoreboa1bsm6o0000q0n8jpptvc9d4ic&tabs.activeTab=SlotTab"
    )
    assert lot_entity_from_url(with_session) == "LF:coreboa1bsm6o0000q0n8jpptvc9d4ic"
    refs = extract_lot_references(f'<a href="{with_session}">Лот 0042126001DS.lot1</a>')
    assert len(refs) == 1
    assert refs[0].entity.startswith("LF:")
    assert "jsessionid" not in refs[0].detail_url
    assert "tabs.activeTab" not in refs[0].detail_url
    for url in (
        "http://etp.alfalot.ru/app/LotCard/page?LotCard.lotEntity=LT%3A0026026025DS.lot1",
        "https://evil.example/app/LotCard/page?LotCard.lotEntity=LT%3A0026026025DS.lot1",
        "https://etp.alfalot.ru/app/OfferCard/page?LotCard.lotEntity=LT%3A0026026025DS.lot1",
        "https://etp.alfalot.ru/app/LotCard/page?LotCard.lotEntity=LT%3Aone&LotCard.lotEntity=LT%3Atwo",
    ):
        assert lot_entity_from_url(url) is None


def test_distinct_lots_of_one_procedure_never_inherit_parent_row_fields() -> None:
    html = """
    <table><tr><td>Процедура: земля, автомобиль, задаток 999000 руб.</td>
      <td><a href="/app/LotCard/page?LotCard.lotEntity=LT%3A0026026017DS.lot1">Участок 1</a></td>
      <td><a href="/app/LotCard/page?LotCard.lotEntity=LT%3A0026026017DS.lot2">Участок 2</a></td>
    </tr></table>
    """
    refs = extract_lot_references(html)
    assert [r.entity for r in refs] == [
        "LT:0026026017DS.lot1", "LT:0026026017DS.lot2",
    ]
    assert all("999000" not in r.row_text for r in refs)
    assert all(r.property_candidate == "unknown" for r in refs)


def test_property_keywords_are_only_unverified_triage_not_import_scope() -> None:
    assert property_candidate("Земельный участок") == "property_candidate_unverified"
    assert property_candidate("Земельный участок и автомобиль") == "mixed_unverified"
    assert property_candidate("Права требования") == "non_property_candidate"


def test_price_requires_individual_exact_label_and_value() -> None:
    assert parse_exact_price("56 000 000,00 руб.") == 5_600_000_000
    assert parse_exact_price("197331000") == 19_733_100_000
    assert parse_exact_price("задаток 5 000 000") is None
    html = """
    <h1>Лот 0026026025DS.lot1</h1>
    <table>
      <tr><th>Начальная цена</th><td>56 000 000 руб.</td></tr>
      <tr><th>Размер задатка</th><td>5 600 000 руб.</td></tr>
      <tr><th>Состояние лота</th><td>Идет прием заявок</td></tr>
      <tr><th>Дата окончания подачи заявок</th><td>30.11.2026</td></tr>
    </table>
    """
    fields = extract_detail_fields(
        html,
        "https://etp.alfalot.ru/app/LotCard/page?LotCard.lotEntity=LT%3A0026026025DS.lot1",
    )
    assert fields["start_price_kopecks"] == 5_600_000_000
    assert fields["status"] == "Идет прием заявок"
    assert fields["current_price_kopecks"] is None
    assert fields["application_end"] == "30.11.2026"
    assert fields["property_scope_proven"] is False


def test_official_detail_price_label_distinct_from_deposit_and_step() -> None:
    official_style = """
    <h1>Лот продажи 0020626010DS.lot1</h1>
    <table>
      <tr><th>Начальная (минимальная) цена договора (лота)</th><td>2 624 497.53 руб.</td></tr>
      <tr><th>Размер обеспечения заявки</th><td>500 000.00 руб.</td></tr>
      <tr><th>Шаг торгов</th><td>131 224.88 руб.</td></tr>
      <tr><th>Состояние лота</th><td>Работа комиссии</td></tr>
    </table>
    """
    detail = extract_detail_fields(
        official_style,
        "https://etp.alfalot.ru/app/LotCard/page?LotCard.lotEntity=LT%3Acoreboa1bsm6o0000q1pk823cqa7he10",
    )
    assert detail["visible_lot_number"] == "0020626010DS.lot1"
    assert detail["start_price_kopecks"] == 262449753
    assert detail["current_price_kopecks"] is None
    assert detail["status"] == "Работа комиссии"


def test_conflicting_initial_price_rows_fail_closed() -> None:
    html = """
    <table>
      <tr><td>Начальная цена</td><td>2 000 000 руб.</td></tr>
      <tr><td>Начальная цена</td><td>5 000 000 руб.</td></tr>
    </table>
    """
    with pytest.raises(AlfalotContractError, match="conflicting initial"):
        extract_detail_fields(
            html,
            "https://etp.alfalot.ru/app/LotCard/page?LotCard.lotEntity=LT%3Acoreboa1bsm6o0000q1pk823cqa7he10",
        )


def test_public_offer_initial_and_current_prices_do_not_inherit_deposit_or_schedule() -> None:
    # Sanitized, short shape of official 0040526003DS.lot1 public-offer labels.
    html = """
    <h1>Лот продажи 0040526003DS.lot1</h1>
    <table>
      <tr><td>Номер процедуры</td><td>0040526003DS</td></tr>
      <tr><td>Номер лота</td><td>1</td></tr>
      <tr><td>Вид торговой процедуры</td><td>Продажа имущества</td></tr>
      <tr><td>Начальная (минимальная) цена договора (лота)</td>
          <td>25 984 647.00 руб. (в т.ч. НДС 20%)</td></tr>
      <tr><td>Текущая минимальная цена договора (лота)</td>
          <td>7 795 394.10 руб. (в т.ч. НДС 20%)</td></tr>
      <tr><td>Конечная минимальная цена договора (лота) («цена отсечения»)</td>
          <td>2 598 464.70 руб.</td></tr>
      <tr><td>Размер обеспечения заявки</td><td>779 539.41 руб.</td></tr>
      <tr><td>Состояние лота</td><td>Заключение договора</td></tr>
    </table>
    """
    fields = extract_detail_fields(
        html,
        "https://etp.alfalot.ru/app/LotCard/page?LotCard.lotEntity=LF%3Acoreboa1bsm6o0000pvircbdokt9v9ug",
    )
    assert fields["visible_lot_number"] == "0040526003DS.lot1"
    assert fields["procedure_number"] == "0040526003DS"
    assert fields["lot_number"] == "1"
    assert fields["procedure_type"] == "Продажа имущества"
    assert fields["start_price_kopecks"] == 2598464700
    assert fields["current_price_kopecks"] == 779539410
    assert fields["status"] == "Заключение договора"
    assert fields["property_scope_proven"] is False
    assert parse_exact_price("55 000 000.00 руб. (без учета НДС)") == 5_500_000_000


def test_procedure_active_but_each_lot_cancelled_must_not_inherit_procedure_status() -> None:
    # Official 0026026007DS has an accepting-applications procedure status,
    # but its ten separately identified parking-space lots were cancelled.
    procedure_html = """
    <h1>Процедура 0026026007DS</h1>
    <table><tr><td>Состояние процедуры</td><td>Идет прием заявок</td></tr></table>
    """
    with pytest.raises(AlfalotContractError, match="individual LotCard"):
        extract_detail_fields(
            procedure_html,
            "https://etp.alfalot.ru/app/OfferCard/page?OfferCard.offerUUID=Scoreboa1bsm6o0000ps0krvk345mtvik",
        )
    detail_html = """
    <h1>Лот продажи 0026026007DS.lot4</h1>
    <table>
      <tr><td>Номер процедуры</td><td>0026026007DS</td></tr>
      <tr><td>Номер лота</td><td>4</td></tr>
      <tr><td>Состояние лота</td><td>Отказ от проведения</td></tr>
    </table>
    """
    fields = extract_detail_fields(
        detail_html,
        "https://etp.alfalot.ru/app/LotCard/page?LotCard.lotEntity=LT%3Acoreboa1bsm6o0000ps0l0gfbnldeo74",
    )
    assert fields["status"] == "Отказ от проведения"
    assert fields["status"] != "Идет прием заявок"


def test_two_complete_identical_walks_produce_reproducible_id_digest() -> None:
    first = [_page(1, ("LT:0000000001DS.lot1",)), _page(2, ("LT:0000000002DS.lot1",), True)]
    proof = verify_two_walks(first, list(first))
    assert proof["distinct_ids"] == 2
    assert proof["terminal_verified"] is True
    assert proof["production_enabled"] is False
    assert proof["property_scope_verified"] is False


@pytest.mark.parametrize("other", [
    [_page(1, ("LT:0000000001DS.lot1",)), _page(2, ("LT:0000000001DS.lot1",), True)],
    [_page(1, ("LT:0000000001DS.lot1",), True)],
    [_page(1, ("LT:0000000001DS.lot1",)), _page(3, ("LT:0000000002DS.lot1",), True)],
    [_page(1, ("LT:0000000001DS.lot1",)), _page(2, ("LT:0000000003DS.lot1",), True)],
])
def test_two_walks_reject_duplicates_missing_pages_or_changed_id_set(other: list[PageProof]) -> None:
    valid = [_page(1, ("LT:0000000001DS.lot1",)), _page(2, ("LT:0000000002DS.lot1",), True)]
    with pytest.raises(AlfalotContractError):
        verify_two_walks(valid, other)
