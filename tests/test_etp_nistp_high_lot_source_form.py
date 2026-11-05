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
