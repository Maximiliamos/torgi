from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from bankrotai.connectors.etp_sources.aukcioncenter import (
    ACTIVE_FILTERS,
    browser_url,
    catalogue_params,
    catalogue_url,
    declared_pages,
    detail_request_url,
    discover_lot_numbers,
    discover_trade_subroutes,
    has_next_page,
    lot_index_url,
    observation_digest,
    parse_detail,
    parse_listing,
    parse_procedure_observations,
    status_filter_options,
    terminal_page_contract,
)


FIXTURES = Path(__file__).parent / "fixtures" / "etp" / "aukcioncenter"


@dataclass(frozen=True)
class _Profile:
    source_id: str = "aukcioncenter.ru"
    platform_name: str = "Аукцион-центр"
    platform_code: str = "auctioncenter"


PROFILE = _Profile()


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_provider_status_filter_values_are_read_from_source_form():
    html = """
    <select name="processStatus">
      <option value="any">Все</option>
      <option value="BID_SUBMISSION">Идёт приём заявок</option>
      <option value="COMPLETED">Торги завершены</option>
      <option value="CANCELLED">Торги отменены</option>
    </select>
    """
    options = status_filter_options(html)
    assert options["идёт приём заявок"] == "BID_SUBMISSION"
    assert options["торги завершены"] == "COMPLETED"
    assert options["торги отменены"] == "CANCELLED"


def test_active_filter_contract_is_owned_by_source_module():
    assert ACTIVE_FILTERS["processStatus"] == "BID_SUBMISSION"
    assert catalogue_params(2)["page"] == "2"
    url = catalogue_url(2)
    assert "processStatus=BID_SUBMISSION" in url
    assert "page=2" in url
    assert "show=current" not in url


def test_active_catalogue_splits_procedure_and_classifies_property_vs_vehicle():
    lots = parse_listing(PROFILE, _fixture("listing_mixed.html"), page=1)
    assert [lot.external_id for lot in lots] == [
        "aukcioncenter.ru:103550001:1",
        "aukcioncenter.ru:103550001:2",
    ]
    land, vehicle = lots
    assert land.procedure_number == "7600-ОАОФ"
    assert land.category == "land"
    assert land.raw_data["eligible_property"] is True
    assert land.cadastral_number == "76:01:000001:101"
    assert land.area == 1000
    assert "Тестовая область" in (land.address or "")
    assert vehicle.category == "vehicle"
    assert vehicle.raw_data["eligible_property"] is False
    assert all(lot.start_price is None and lot.current_price is None for lot in lots)
    assert all(lot.auction_status == "unknown" for lot in lots)
    assert land.application_start_at.isoformat() == "2026-11-01T09:00:00"
    assert land.application_deadline.isoformat() == "2026-12-01T18:00:00"
    assert land.raw_data["lot_number"] == 1
    assert land.raw_data["individual_lot_identity_verified"] is True
    assert land.raw_data["listing_initial_price_verified"] is False
    assert land.raw_data["listing_current_price_verified"] is False


def test_explicit_per_lot_listing_initial_price_is_safe_but_deposit_is_not_price():
    html = """
    <table><tr onclick="go('/trade/view/purchase/general.html?id=103550101')">
      <td>7606-ОАОФ</td>
      <td>
        Лот №1: Земельный участок, кадастровый номер 76:01:000001:501.
        Начальная цена продажи – 1 234 567,89 руб. Размер задатка 123 456,78 руб.
        Лот №2: Автомобиль. Размер задатка 500 000 руб.
      </td>
      <td>идёт приём заявок</td>
      <td>07.11.2026 09:00</td><td>07.12.2026 18:00</td>
    </tr></table>
    """
    lots = parse_listing(PROFILE, html)
    assert [lot.start_price for lot in lots] == [1_234_567.89, None]
    assert lots[0].raw_data["listing_initial_price_verified"] is True
    assert lots[1].raw_data["listing_initial_price_verified"] is False
    assert all(lot.current_price is None for lot in lots)


def test_bid_submission_filter_without_explicit_status_stays_unknown():
    html = """
    <table><tr onclick="go('/trade/view/purchase/general.html?id=103550102')">
      <td>7607-ОАОФ</td>
      <td>Лот №1: Квартира, кадастровый номер 77:01:000001:501.</td>
      <td>08.11.2026 09:00</td><td>08.12.2026 18:00</td>
    </tr></table>
    """
    lot = parse_listing(PROFILE, html, active_filter=True)[0]
    assert lot.auction_status == "unknown"
    assert lot.raw_data["source_status_text"] is None


def test_single_lot_requires_source_lot_number_in_url_instead_of_inventing_lot_one():
    html = """
    <table><tr>
      <td>7601-ОАОФ</td>
      <td><a href="/trade/view/purchase/general.html?id=103550002&amp;lotNumber=1">
        Квартира, кадастровый номер 77:01:000001:12, площадь 40 кв. м
      </a></td>
      <td>02.11.2026 09:00</td><td>02.12.2026 18:00</td>
    </tr></table>
    """
    lots = parse_listing(PROFILE, html)
    assert [lot.external_id for lot in lots] == ["aukcioncenter.ru:103550002:1"]
    assert parse_listing(PROFILE, html.replace("&amp;lotNumber=1", "")) == []


def test_mixed_property_and_nonproperty_inside_one_lot_is_not_property_eligible():
    html = """
    <table><tr onclick="go('/trade/view/purchase/general.html?id=103550003')">
      <td>7602-ОАОФ</td>
      <td>Лот №1: Нежилое помещение и отдельный автомобиль как единый комплект.</td>
      <td>03.11.2026 09:00</td><td>03.12.2026 18:00</td>
    </tr></table>
    """
    lot = parse_listing(PROFILE, html)[0]
    assert lot.category == "mixed"
    assert lot.raw_data["eligible_property"] is False


def test_pagination_and_terminal_contract_are_source_specific():
    page1 = """
    <table><tr onclick="go('/trade/view/purchase/general.html?id=103550020')">
      <td>7610-ОАОФ</td><td>Лот №1: Квартира 77:01:000001:20.</td>
      <td>01.11.2026 09:00</td><td>01.12.2026 18:00</td>
    </tr></table>
    <a href="?page=2&processStatus=BID_SUBMISSION">2</a>
    <a href="?page=6&processStatus=BID_SUBMISSION">6</a>
    """
    last = """
    <table><tr onclick="go('/trade/view/purchase/general.html?id=103550021')">
      <td>7611-ОАОФ</td><td>Лот №1: Земельный участок 77:01:000001:21.</td>
      <td>02.11.2026 09:00</td><td>02.12.2026 18:00</td>
    </tr></table>
    <a href="?page=6&processStatus=BID_SUBMISSION">6</a>
    """
    assert declared_pages(page1) == (2, 6)
    assert has_next_page(page1, 1) is True
    last_obs = parse_procedure_observations(last)
    assert observation_digest(last_obs) == observation_digest(last_obs)
    assert terminal_page_contract(last, "<html></html>", last_page=6) == (
        True, "empty_after_declared_last",
    )
    assert terminal_page_contract(last, last, last_page=6) == (
        True, "overflow_clamped_to_declared_last",
    )


def test_route_discovery_redacts_query_values_and_external_hosts():
    html = """
    <a href="/etp/trade/inner-view-lots.html?id=103550004&lotNumber=2">Лоты</a>
    <a href="https://example.com/trade/secret.html?id=1">external</a>
    <script>var endpoint='/etp/trade/inner-view-price.html?id=103550004&page=1';</script>
    """
    routes = discover_trade_subroutes(
        html,
        base_url="https://aukcioncenter.ru/trade/view/purchase/general.html?id=103550004",
    )
    assert {item["path"] for item in routes} == {
        "/etp/trade/inner-view-lots.html",
        "/etp/trade/inner-view-price.html",
    }
    assert {tuple(item["query_keys"]) for item in routes} == {
        ("id", "lotNumber"),
        ("id", "page"),
    }
    assert all("103550004" not in str(item) for item in routes)


def test_real_detail_contract_is_inner_lot_loader_not_general_procedure_page():
    external_id = "aukcioncenter.ru:103550004:2"
    assert browser_url(external_id).endswith("id=103550004&lotNumber=2")
    detail = detail_request_url(external_id)
    assert "/etp/trade/inner-view-lots.html?" in detail
    assert "perspective=inline" in detail
    assert "id=103550004" in detail
    assert "page=1" in detail
    assert "lotNumber=2" in detail
    assert "lotNumber=" in lot_index_url("103550004")
    with pytest.raises(ValueError, match="detail_identity_mismatch"):
        parse_detail(
            PROFILE,
            "<div>Лот №2: Квартира, начальная цена 1 000 000 руб.</div>",
            detail_url_value=browser_url(external_id),
            external_id=external_id,
        )


def test_detail_price_and_property_fields_are_bound_only_to_selected_lot():
    external_id = "aukcioncenter.ru:103550005:1"
    detail = parse_detail(
        PROFILE,
        _fixture("inner_lot.html"),
        detail_url_value=detail_request_url(external_id),
        external_id=external_id,
    )
    assert detail.start_price == 1_250_000.50
    assert detail.current_price == 1_100_000.25
    assert detail.cadastral_number == "76:01:000001:101"
    assert detail.area == 1200
    assert "Тестовая область" in (detail.address or "")
    assert detail.raw_data["detail_start_price_verified"] is True
    assert detail.raw_data["detail_current_price_verified"] is True
    assert detail.raw_data["detail_endpoint"] == "/etp/trade/inner-view-lots.html"


def test_deposit_and_auction_step_are_never_used_as_initial_price():
    external_id = "aukcioncenter.ru:103550012:1"
    html = """
    <div>Лот №1: Земельный участок 76:01:000001:12, площадь 700 кв. м.
    Размер задатка 250 000 руб. Шаг аукциона 75 000 руб.</div>
    """
    detail = parse_detail(
        PROFILE, html, detail_url_value=detail_request_url(external_id),
        external_id=external_id,
    )
    assert detail.start_price is None
    assert detail.current_price is None
    assert detail.raw_data["detail_start_price_verified"] is False


def test_application_end_date_does_not_become_closed_status():
    external_id = "aukcioncenter.ru:103550006:1"
    html = """
    <html><body>
      <p>Дата окончания приема заявок: 01.01.2020 18:00</p>
      <div>Лот №1: Квартира, площадь 35 кв. м,
      кадастровый номер 77:01:000001:12. Начальная цена 2 000 000 руб.</div>
    </body></html>
    """
    detail = parse_detail(
        PROFILE, html, detail_url_value=detail_request_url(external_id),
        external_id=external_id,
    )
    assert detail.auction_status == "unknown"
    assert detail.application_deadline.isoformat() == "2020-01-01T18:00:00"


@pytest.mark.parametrize(
    ("source_text", "expected", "lifecycle"),
    [
        ("торги завершены", "closed", "finished"),
        ("торги отменены", "closed", "cancelled"),
        ("идёт приём заявок", "active", "active"),
    ],
)
def test_only_explicit_source_lifecycle_marker_sets_status(source_text, expected, lifecycle):
    external_id = "aukcioncenter.ru:103550007:1"
    html = f"""
    <html><body><p>{source_text}</p>
      <div>Лот №1: Квартира, площадь 35 кв. м,
      кадастровый номер 77:01:000001:12. Начальная цена 2 000 000 руб.</div>
    </body></html>
    """
    detail = parse_detail(
        PROFILE, html, detail_url_value=detail_request_url(external_id),
        external_id=external_id,
    )
    assert detail.auction_status == expected
    assert detail.raw_data["source_status_text"] == source_text
    assert detail.raw_data["source_lifecycle"] == lifecycle


def test_area_uses_declared_area_not_measurement_uncertainty():
    html = """
    <table><tr onclick="go('/trade/view/purchase/general.html?id=103550009')">
      <td>7604-ОАОФ</td>
      <td>Лот №1: Земельный участок, площадь 1000 +/- 22,1 кв. м,
      кадастровый номер 76:01:000001:444.</td>
      <td>05.11.2026 09:00</td><td>05.12.2026 18:00</td>
    </tr></table>
    """
    assert parse_listing(PROFILE, html)[0].area == 1000


def test_announced_status_is_scheduled_even_in_active_filtered_register():
    html = """
    <table><tr onclick="go('/trade/view/purchase/general.html?id=103550010')">
      <td>7605-ОАОФ</td>
      <td>Лот №1: Квартира, площадь 42 кв. м, кадастровый номер 77:01:000001:99.</td>
      <td>объявлены</td>
      <td>06.11.2026 09:00</td><td>06.12.2026 18:00</td>
    </tr></table>
    """
    lot = parse_listing(PROFILE, html)[0]
    assert lot.auction_status == "scheduled"
    assert lot.raw_data["source_lifecycle"] == "scheduled"


def test_inner_endpoint_isolates_requested_lot_inside_multi_lot_payload():
    external_id = "aukcioncenter.ru:103550011:1"
    html = """
    <div class="lot-list">
      <div id="lotNumber1">
        Лот №1: Земельный участок, кадастровый номер 76:01:000001:1.
        Начальная цена 1 000 000 руб.
      </div>
      <div id="lotNumber2">
        Лот №2: Земельный участок, кадастровый номер 76:01:000001:2.
        Начальная цена 2 000 000 руб.
      </div>
    </div>
    """
    detail = parse_detail(
        PROFILE, html, detail_url_value=detail_request_url(external_id),
        external_id=external_id,
    )
    assert detail.start_price == 1_000_000
    assert detail.cadastral_number == "76:01:000001:1"
    assert "2 000 000" not in detail.description
    assert "76:01:000001:2" not in detail.description


def test_flat_multi_lot_payload_without_safe_right_boundary_fails_closed():
    external_id = "aukcioncenter.ru:103550011:2"
    html = """
    <div>
      Лот №1: Земельный участок, кадастровый номер 76:01:000001:1.
      Начальная цена 1 000 000 руб.
      Лот №2: Земельный участок, кадастровый номер 76:01:000001:2.
      Начальная цена 2 000 000 руб.
      Общие условия процедуры: начальная цена 99 000 000 руб.
    </div>
    """
    with pytest.raises(ValueError, match="individual_lot_section_not_proven"):
        parse_detail(
            PROFILE, html, detail_url_value=detail_request_url(external_id),
            external_id=external_id,
        )


def test_lot_index_discovery_reads_only_explicit_provider_lot_numbers():
    html = """
    <div>Лот №3</div>
    <a href="/etp/trade/inner-view-lots.html?id=103550099&lotNumber=4">next</a>
    <input name="lotNumber" value="5">
    """
    assert discover_lot_numbers(html) == (3, 4, 5)
