"""No-network tests for exact failed-card structural evidence."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from bs4 import BeautifulSoup

import bankrotai.services.etp_failed_card_structure as probe


def _prior_file(tmp_path, source="nistp.ru", last_page=142):
    filename = tmp_path / "previous.json"
    external_id = (
        "nistp.ru:502379:139" if source == "nistp.ru" else "cdtrf:390579"
    )
    filename.write_text(json.dumps({
        "source": source,
        "mode": "bounded_independent_readonly_lot_detail_sample",
        "last_page_from_previous_full_walk": last_page,
        "source_accepted": False,
        "errors": [{
            "page": 10, "external_id": external_id,
            "stage": "detail", "code": "NistpCardError",
        }],
    }), encoding="utf-8")
    return filename


def test_only_exact_prior_failed_lot_ids_allowed(tmp_path):
    report = _prior_file(tmp_path)
    result = probe.previous_failures(report, "nistp.ru", 142)
    assert result == [{"page": 10, "external_id": "nistp.ru:502379:139"}]
    with pytest.raises(ValueError, match="source/scope"):
        probe.previous_failures(report, "cdtrf.ru", 64)
    data = json.loads(report.read_text(encoding="utf-8"))
    data["errors"][0]["external_id"] = "https://malicious.test"
    report.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="detail failure identity"):
        probe.previous_failures(report, "nistp.ru", 142)


def test_nistp_structure_shows_absent_large_lot_section():
    soup = BeautifulSoup(
        "<table id='table_lot_1'><tbody><tr>"
        "<td class='label'>Номер лота</td><td>1</td>"
        "</tr></tbody></table>", "html.parser",
    )
    report = probe.nistp_structure(soup, [139])
    assert report["numbered_table_count"] == 1
    assert report["highest_table_number"] == 1
    assert report["requested_lots"][0]["requested_numbered_table_present"] is False


def test_nistp_structure_distinguishes_missing_description_label():
    soup = BeautifulSoup(
        "<table id='table_lot_3'><thead><tr><th class='table_header'>"
        "Лот № 3</th></tr></thead><tbody>"
        "<tr><td class='label'>Номер лота</td><td>3</td></tr>"
        "<tr><td class='label'>Наименование имущества</td><td>Земля</td></tr>"
        "<tr><td class='label'>Начальная цена</td><td>100</td></tr>"
        "<tr><td class='label'>Статус торгов</td><td>Прием заявок</td></tr>"
        "<tr><td class='label'>Сведения об имуществе</td><td>Сведения</td></tr>"
        "</tbody></table>", "html.parser",
    )
    result = probe.nistp_structure(soup, [3])
    assert result["requested_lots"][0]["fields"]["expected_description_labels"] == 0
    assert result["requested_lots"][0]["fields"]["alternative_description_labels"] == 1
    assert result["tables_without_exact_description_label"] == 1


def test_cdtrf_title_only_mismatch_is_not_identity_mismatch(monkeypatch):
    fake_html = BeautifulSoup(
        "<div class='lot__header'>Код торгов: 390364"
        "<h1>Название на странице</h1></div>"
        "<div class='lot-sidebar__price-val'>1 000</div>",
        "html.parser",
    )
    monkeypatch.setattr(
        probe, "_ssr_lot",
        lambda soup: (
            {"tradeLotId": 390899, "name": "Другое название", "priceBegin": 1000},
            {"tradeId": 390364},
        ),
    )
    result = probe.cdtrf_structure(fake_html, "390364", "390899")
    assert result["html_trade_code_matches_api"] is True
    assert result["nuxt_trade_id_matches_api"] is True
    assert result["nuxt_lot_id_matches_api"] is True
    assert result["html_title_matches_nuxt_lot_name"] is False
    assert result["title_only_mismatch_if_all_ids_match"] is True
    assert result["html_and_nuxt_price_match"] is True


def test_cdtrf_wrong_ssr_lot_never_classified_as_title_only(monkeypatch):
    fake_html = BeautifulSoup(
        "<div class='lot__header'>Код торгов: 390364"
        "<h1>Название на странице</h1></div>", "html.parser",
    )
    monkeypatch.setattr(
        probe, "_ssr_lot",
        lambda soup: (
            {"tradeLotId": 390898, "name": "Другое название"},
            {"tradeId": 390364},
        ),
    )
    report = probe.cdtrf_structure(fake_html, "390364", "390899")
    assert report["nuxt_lot_id_matches_api"] is False
    assert report["title_only_mismatch_if_all_ids_match"] is False


def test_nistp_groups_six_large_lots_in_single_request(monkeypatch):
    requested = [
        {"external_id": f"nistp.ru:502379:{n}", "page": i}
        for i, n in enumerate((139, 179, 239, 339, 439, 539), start=1)
    ]
    fetched = []
    soup = BeautifulSoup("<table id='table_lot_1'></table>", "html.parser")

    def fake_http(session, url):
        fetched.append(url)
        return soup, None

    monkeypatch.setattr(probe, "_http_html", fake_http)
    result = probe.scan_nistp(object(), requested)
    assert len(fetched) == 1
    assert len(result) == 6
    assert all(row["requested_section"]["requested_numbered_table_present"] is False for row in result)
