"""Offline safety contracts for NISTP exact high-lot source-search probe."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

import bankrotai.services.etp_nistp_high_lot_source_form as probe


TRADE_CODE = "72113-ОАОФ"


def _form_html(*, with_filters=True) -> bytes:
    states = ""
    if with_filters:
        states = "".join(
            f'<input name="trade_state[]" value="{state}" checked>'
            for state in probe.ACTIVE_SOURCE_STATES
        )
    return (
        "<html><form id='nodesearch'>"
        f"<input name='trade_number' value='{TRADE_CODE}'>"
        "<input name='lot_number' value='259'>"
        f"{states}</form><p>No verified rows</p></html>"
    ).encode("utf-8")


def test_exact_search_query_is_provider_owned_and_strict() -> None:
    params = probe.search_params(TRADE_CODE, 259)
    assert ("trade_number", TRADE_CODE) in params
    assert ("lot_number", "259") in params
    assert ("view_type", "lot") in params
    assert sum(key == "trade_state[]" for key, _ in params) == 3
    with pytest.raises(ValueError):
        probe.search_params("502379", 259)
    with pytest.raises(ValueError):
        probe.search_params(TRADE_CODE, 5000)


def test_form_echo_without_exact_catalogue_row_never_proves_detail() -> None:
    output = probe.inspect_exact_source_search(
        _form_html(), trade_code=TRADE_CODE, number=259,
    )
    assert output["form_trade_and_lot_echo_proven"] is True
    assert output["selected_active_filters_visible"] is True
    assert output["exact_matching_rows"] == 0
    assert output["exact_search_scope_proven"] is False
    assert output["independent_lot_detail_verified"] is False
    assert output["source_accepted"] is False


def test_matching_catalogue_row_is_still_not_independent_detail(monkeypatch) -> None:
    monkeypatch.setattr(
        probe, "parse_nistp_lot_observations",
        lambda *args, **kwargs: [{
            "provider_external_id": "nistp.ru:502379:259",
            "source_status_text": "Прием заявок",
            "listing_price_rub": 1000.0,
        }],
    )
    output = probe.inspect_exact_source_search(
        _form_html(), trade_code=TRADE_CODE, number=259,
    )
    assert output["exact_search_scope_proven"] is True
    assert output["exact_matching_rows"] == 1
    assert output["independent_lot_detail_verified"] is False
    assert output["source_accepted"] is False


def test_unmatched_filter_or_other_id_fails_scope(monkeypatch) -> None:
    monkeypatch.setattr(
        probe, "parse_nistp_lot_observations",
        lambda *args, **kwargs: [
            {"provider_external_id": "nistp.ru:502379:259",
             "source_status_text": "Прием заявок", "listing_price_rub": 1000.0},
            {"provider_external_id": "nistp.ru:502379:1",
             "source_status_text": "Прием заявок", "listing_price_rub": 200.0},
        ],
    )
    output = probe.inspect_exact_source_search(
        _form_html(with_filters=False), trade_code=TRADE_CODE, number=259,
    )
    assert output["other_ids_returned_count"] == 1
    assert output["selected_active_filters_visible"] is False
    assert output["exact_search_scope_proven"] is False


def test_no_network_probe_emits_seven_results_and_never_accepts(monkeypatch):
    calls = []

    class Session:
        def get(self, url, **kwargs):
            calls.append((url, kwargs))
            if url == probe.TRADE_URL:
                body = (
                    "<html><h1 class='node_header'>"
                    "Идентификационный номер 72113-ОАОФ"
                    "</h1></html>"
                )
            else:
                body = _form_html().decode("utf-8")
            return SimpleNamespace(
                status_code=200,
                content=body.encode("utf-8"),
                headers={"Content-Type": "text/html; charset=utf-8"},
            )

    report = probe.run_probe(session=Session(), sleep=lambda _: None)
    assert len(calls) == 8
    assert len(report["search_rows"]) == 7
    assert report["independent_lot_detail_verified"] is False
    assert report["source_accepted"] is False
    assert report["production_database_writes"] == 0
    assert report["archival_allowed"] is False
    assert all(kwargs["allow_redirects"] is False for _, kwargs in calls)


def test_exact_row_link_census_never_invents_detail_and_does_not_emit_urls():
    from bs4 import BeautifulSoup

    row = (
        "<tr><td>72113-ОАОФ</td><td>организатор</td>"
        "<td>Лот № 259: машино-место "
        "<a href='/bankrot/trade_view.php?trade_nid=502379'>Открыть</a>"
        "<a href='/bankrot/lot_view.php?lot_nid=712345&secret=private'>Другой маршрут</a>"
        "<a href='https://evil.example/collect?token=secret'>Внешняя ссылка</a>"
        "</td><td>10000</td><td></td><td></td><td>Прием заявок</td></tr>"
    )
    shapes = probe._safe_row_link_shapes(
        BeautifulSoup(row, "html.parser"),
        trade_code="72113-ОАОФ", lot_number=259,
    )
    assert len(shapes) == 2
    trade = next(x for x in shapes if x["is_only_trade_page_route"])
    assert trade["path"] == "/bankrot/trade_view.php"
    candidate = next(x for x in shapes if x["has_lot_scoped_parameter"])
    assert candidate["path"] == "/bankrot/lot_view.php"
    assert "lot_nid" in candidate["query_field_names"]
    assert "712345" not in repr(shapes)
    assert "private" not in repr(shapes)
    assert "evil" not in repr(shapes)


def test_unrelated_or_malformed_lot_links_do_not_create_false_route_evidence():
    from bs4 import BeautifulSoup

    html = (
        "<table>"
        "<tr><td>72113-ОАОФ</td><td></td><td>Лот № 139 "
        "<a href='/bankrot/lot_view.php?lot_nid=100'>Другая карточка</a>"
        "</td><td>1</td><td></td><td></td><td>Прием заявок</td></tr>"
        "<tr><td>72113-ОАОФ</td><td></td><td>Лот № 259 "
        "<a href='https://nistp.ru:bad/bankrot/lot_view.php?lot_nid=259'>bad port</a>"
        "<a href='javascript:alert(1)'>untrusted</a>"
        "</td><td>1</td><td></td><td></td><td>Прием заявок</td></tr>"
        "</table>"
    )
    shapes = probe._safe_row_link_shapes(
        BeautifulSoup(html, "html.parser"),
        trade_code="72113-ОАОФ", lot_number=259,
    )
    assert shapes == []


def test_link_candidates_do_not_upgrade_catalogue_scope_to_detail(monkeypatch):
    row = (
        "<html><form id='nodesearch'>"
        "<input name='trade_number' value='72113-ОАОФ'>"
        "<input name='lot_number' value='259'>"
        + "".join(
            f'<input name="trade_state[]" value="{s}" checked>'
            for s in probe.ACTIVE_SOURCE_STATES
        )
        + "</form><table><tr><td>72113-ОАОФ</td><td></td>"
        "<td>Лот № 259: <a href='/bankrot/lot_view.php?lot_nid=777'>Открыть</a></td>"
        "<td>100</td><td></td><td></td><td>Прием заявок</td>"
        "</tr></table></html>"
    )
    monkeypatch.setattr(
        probe, "parse_nistp_lot_observations",
        lambda *args, **kwargs: [{
            "provider_external_id": "nistp.ru:502379:259",
            "source_status_text": "Прием заявок",
            "listing_price_rub": 100.0,
        }],
    )
    output = probe.inspect_exact_source_search(
        row.encode("utf-8"), trade_code="72113-ОАОФ", number=259,
    )
    assert output["exact_search_scope_proven"] is True
    assert output["distinct_lot_scoped_route_candidates"] == 1
    assert output["independent_lot_detail_endpoint_verified"] is False
    assert output["independent_lot_detail_verified"] is False
    assert output["source_accepted"] is False


def test_link_census_requires_source_valid_lot_number_delimiter():
    """Row links cannot confer evidence when the provider lot header is invalid."""
    from bs4 import BeautifulSoup

    malformed_row = (
        "<tr><td>72113-ОАОФ</td><td></td>"
        "<td>Лот № 259 <a href='/bankrot/lot_view.php?lot_nid=777'>Link</a></td>"
        "<td>100</td><td></td><td></td><td>Прием заявок</td></tr>"
    )
    assert probe._safe_row_link_shapes(
        BeautifulSoup(malformed_row, "html.parser"),
        trade_code="72113-ОАОФ", lot_number=259,
    ) == []
