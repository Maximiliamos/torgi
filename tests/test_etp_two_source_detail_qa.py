"""No-network regression coverage for sampled independent NISTP/CDTRF details."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import bankrotai.services.etp_two_source_detail_qa as qa


def _detail(source: str, identity: str, *, price: float, status: str):
    return SimpleNamespace(
        external_id=identity, source_system=source,
        detail_level="detail" if source == "nistp.ru" else "public_detail",
        raw_data=(
            {"individual_lot_identity_verified": True}
            if source == "nistp.ru"
            else {"trade_id": "390364", "trade_lot_id": "390899", "property_right_kind": "ownership"}
        ),
        start_price=price, auction_status=status, category="land",
        title="Земельный участок", description="Земельный участок, площадь 600 кв. м",
        cadastral_number="32:10:0010101:42", address=None, deposit_amount=None,
    )


def test_spread_pages_are_unique_cover_first_last_and_reject_unsafe_bounds() -> None:
    pages = qa.spaced_pages(142, 30)
    assert len(pages) == len(set(pages)) == 30
    assert pages[0] == 1
    assert pages[-1] == 142
    assert len(qa.planned_pages(64, 30)) >= 30
    with pytest.raises(ValueError):
        qa.spaced_pages(2, 3)
    with pytest.raises(ValueError):
        qa.planned_pages(64, 0)


def test_price_comparison_does_not_confuse_missing_or_subkopeck_values() -> None:
    assert qa._same_initial_price("100.00", 100.0)
    assert not qa._same_initial_price("100", 200)
    assert not qa._same_initial_price(None, 100)
    assert not qa._same_initial_price("NaN", 100)
    assert not qa._same_initial_price("100.001", 100)


def _nistp_observation(status: str = "Прием заявок") -> dict:
    return {
        "provider_external_id": "nistp.ru:501045:2",
        "is_property": True,
        "source_status_text": status,
        "listing_price_rub": 100.0,
    }


class NistpStub:
    def __init__(self, *, detail_status: str = "active", price: float = 100.0):
        self._last_response = SimpleNamespace(content=b"<html></html>")
        self.detail_status = detail_status
        self.price = price

    async def search(self, _filters, cursor=None):
        assert cursor == "1"
        return SimpleNamespace(items=[
            SimpleNamespace(external_id="nistp.ru:501045:2"),
        ])

    async def fetch_lot(self, identity):
        return _detail("nistp.ru", identity, price=self.price, status=self.detail_status)


def test_nistp_sample_verifies_detail_without_granting_source_acceptance(monkeypatch) -> None:
    monkeypatch.setattr(
        qa, "parse_nistp_lot_observations",
        lambda *args, **kwargs: [_nistp_observation()],
    )
    report = asyncio.run(qa.check_nistp_details(
        1, 1, connector_factory=NistpStub, sleep=lambda seconds: None,
    ))
    assert report["detail_sample_pass"] is True
    assert report["sample_verified"] == 1
    assert report["source_accepted"] is False
    assert report["archival_allowed"] is False
    assert report["production_database_writes"] == 0



def test_nistp_machine_space_not_false_failed_by_generic_other_category(monkeypatch) -> None:
    monkeypatch.setattr(
        qa, "parse_nistp_lot_observations",
        lambda *args, **kwargs: [_nistp_observation()],
    )

    class ParkingStub(NistpStub):
        async def fetch_lot(self, identity):
            detail = await super().fetch_lot(identity)
            detail.title = "Машино-место"
            detail.description = "Машино-место в подземном паркинге"
            detail.category = "other"
            return detail

    report = asyncio.run(qa.check_nistp_details(
        1, 1, connector_factory=ParkingStub, sleep=lambda seconds: None,
    ))
    assert report["detail_sample_pass"] is True
    assert report["observations"][0]["normalized_category_review_required"] is True
    assert report["source_accepted"] is False


def test_nistp_rejects_price_mismatch_and_does_not_mark_pass(monkeypatch) -> None:
    monkeypatch.setattr(
        qa, "parse_nistp_lot_observations",
        lambda *args, **kwargs: [_nistp_observation()],
    )
    report = asyncio.run(qa.check_nistp_details(
        1, 1, connector_factory=lambda: NistpStub(price=99.0),
        sleep=lambda seconds: None,
    ))
    assert report["sample_attempted"] == 1
    assert report["sample_verified"] == 0
    assert report["error_counts"] == {"listing_detail_initial_price_mismatch": 1}
    assert report["detail_sample_pass"] is False


def test_nistp_closed_row_is_not_selected_as_active(monkeypatch) -> None:
    monkeypatch.setattr(
        qa, "parse_nistp_lot_observations",
        lambda *args, **kwargs: [_nistp_observation("Торги завершены")],
    )
    report = asyncio.run(qa.check_nistp_details(
        1, 1, connector_factory=NistpStub, sleep=lambda seconds: None,
    ))
    assert report["sample_attempted"] == 0
    assert report["skipped_pages"] == 1
    assert report["detail_sample_pass"] is False


def _cdtrf_page(_session, page: int) -> dict:
    assert page == 1
    return {"property_candidates": [{
        "trade_id": "390364", "trade_lot_id": "390899",
        "external_id": "cdtrf:390899",
        "listing_price_rub": "100.00",  # actualPrice is not the initial price
    }]}



def test_cdtrf_sample_uses_operator_proven_land_category_41_and_four_statuses() -> None:
    urls = []

    class Session:
        def get(self, url, **kwargs):
            urls.append(url)
            assert kwargs["allow_redirects"] is False
            return SimpleNamespace(
                status_code=200,
                content=b'{"success":true,"totalCount":0,"items":[]}',
                headers={"Content-Type": "application/json"},
                json=lambda: {"success": True, "totalCount": 0, "items": []},
            )

    qa._cdtrf_page(Session(), 1)
    assert len(urls) == 1
    assert "Categories=21%2C22%2C23%2C31%2C32%2C33%2C34%2C41" in urls[0]
    assert "PageSize=100" in urls[0]
    assert "PageNum=1" in urls[0]
    for state in ("Declare", "RecieveReq", "DefinePart", "TradeGo"):
        assert f"{state}=true" in urls[0]


def test_cdtrf_detail_checks_trade_lot_and_initial_without_price_conflation() -> None:
    report = qa.check_cdtrf_details(
        1, 1, session=object(), sleep=lambda seconds: None,
        fetch_page=_cdtrf_page,
        fetch_detail=lambda trade, lot, session: _detail(
            "cdtrf.ru", f"cdtrf:{lot}", price=200.00, status="scheduled",
        ),
    )
    assert report["sample_verified"] == 1
    assert report["detail_sample_pass"] is True
    assert report["observations"][0]["catalogue_actual_price_not_assumed_initial"] is True
    assert report["source_accepted"] is False



def test_cdtrf_repeat_identity_never_counts_as_two_independent_details() -> None:
    source_row = _cdtrf_page(None, 1)
    report = qa.check_cdtrf_details(
        2, 2, session=object(), sleep=lambda seconds: None,
        fetch_page=lambda session, page: source_row,
        fetch_detail=lambda trade, lot, session: _detail(
            "cdtrf.ru", f"cdtrf:{lot}", price=200.00, status="scheduled",
        ),
    )
    assert report["sample_attempted"] == 1
    assert report["error_counts"] == {"duplicate_sample_identity": 1}
    assert report["detail_sample_pass"] is False


def test_cdtrf_wrong_lot_fails_without_acceptance() -> None:
    report = qa.check_cdtrf_details(
        1, 1, session=object(), sleep=lambda seconds: None,
        fetch_page=_cdtrf_page,
        fetch_detail=lambda trade, lot, session: _detail(
            "cdtrf.ru", "cdtrf:999999", price=200.00, status="scheduled",
        ),
    )
    assert report["sample_verified"] == 0
    assert report["error_counts"] == {"detail_identity_unproven": 1}
    assert report["detail_sample_pass"] is False
    assert report["archival_allowed"] is False



def test_cdtrf_http_429_stops_without_further_provider_requests() -> None:
    class RateLimitedSession:
        calls = 0

        def get(self, url, **kwargs):
            self.calls += 1
            return SimpleNamespace(status_code=429, content=b"", headers={})

    session = RateLimitedSession()
    report = qa.check_cdtrf_details(
        2, 2, session=session, sleep=lambda seconds: None,
    )
    assert session.calls == 1
    assert report["error_counts"] == {"SourceAccessError": 1}
    assert report["detail_sample_pass"] is False


def test_cdtrf_closed_detail_cannot_pass_selected_active_sample() -> None:
    report = qa.check_cdtrf_details(
        1, 1, session=object(), sleep=lambda seconds: None,
        fetch_page=_cdtrf_page,
        fetch_detail=lambda trade, lot, session: _detail(
            "cdtrf.ru", f"cdtrf:{lot}", price=200.00, status="closed",
        ),
    )
    assert report["error_counts"] == {"detail_status_out_of_selected_scope": 1}
    assert report["detail_sample_pass"] is False


def test_only_whitelisted_detail_reasons_are_recorded() -> None:
    from bankrotai.connectors.etp_sources.nistp_detail_sections import NistpCardError
    from bankrotai.connectors.registry.cdtrf_public import CdtrfEvidenceError

    assert qa.safe_detail_reason(NistpCardError("missing_trade_code")) == "missing_trade_code"
    assert qa.safe_detail_reason(
        CdtrfEvidenceError("No CDTRF SSR data")
    ) == "missing_nuxt_ssr"
    assert qa.safe_detail_reason(
        NistpCardError("personal details from source")
    ) is None
    assert qa.safe_detail_reason(ValueError("https://secret.example/private")) is None


def test_recheck_failure_pages_are_loaded_from_bounded_previous_report(tmp_path) -> None:
    import json

    report = tmp_path / "nistp-ru-previous.json"
    record = {
        "source": "nistp.ru",
        "mode": "bounded_independent_readonly_lot_detail_sample",
        "last_page_from_previous_full_walk": 142,
        "source_accepted": False,
        "errors": [
            {"page": 10, "code": "NistpCardError"},
            {"page": 15, "code": "NistpCardError"},
            {"page": 10, "code": "NistpCardError"},
        ],
    }
    report.write_text(json.dumps(record), encoding="utf-8")
    assert qa.failed_pages_from_report(
        report, source="nistp.ru", last_page=142,
    ) == [10, 15]
    with pytest.raises(ValueError, match="inconsistent"):
        qa.failed_pages_from_report(report, source="cdtrf.ru", last_page=64)
    record["errors"] = [{"page": 143, "code": "NistpCardError"}]
    report.write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(ValueError, match="Invalid"):
        qa.failed_pages_from_report(report, source="nistp.ru", last_page=142)


def test_recheck_nistp_is_limited_to_supplied_failed_pages(monkeypatch) -> None:
    monkeypatch.setattr(
        qa, "parse_nistp_lot_observations",
        lambda *args, **kwargs: [_nistp_observation()],
    )
    report = asyncio.run(qa.check_nistp_details(
        2, 2, connector_factory=NistpStub,
        sleep=lambda seconds: None, recheck_pages=[1],
    ))
    assert report["sample_requested"] == 1
    assert report["sample_attempted"] == 1
    assert report["sample_verified"] == 1
    assert report["recheck_only_prior_failure_pages"] is True
    assert report["source_accepted"] is False


def test_recheck_cdtrf_is_limited_to_supplied_failed_pages() -> None:
    report = qa.check_cdtrf_details(
        2, 2, session=object(), sleep=lambda seconds: None,
        recheck_pages=[1], fetch_page=_cdtrf_page,
        fetch_detail=lambda trade, lot, session: _detail(
            "cdtrf.ru", f"cdtrf:{lot}", price=200.00, status="scheduled",
        ),
    )
    assert report["sample_requested"] == 1
    assert report["sample_attempted"] == 1
    assert report["sample_verified"] == 1
    assert report["recheck_only_prior_failure_pages"] is True
    assert report["source_accepted"] is False


def test_nistp_failure_report_identifies_exact_lot_stage_and_safe_reason(monkeypatch) -> None:
    from bankrotai.connectors.etp_sources.nistp_detail_sections import NistpCardError

    monkeypatch.setattr(
        qa, "parse_nistp_lot_observations",
        lambda *args, **kwargs: [_nistp_observation()],
    )

    class BrokenCard(NistpStub):
        async def fetch_lot(self, identity):
            raise NistpCardError("requested_lot_missing_in_trade_card")

    report = asyncio.run(qa.check_nistp_details(
        1, 1, connector_factory=BrokenCard,
        sleep=lambda seconds: None, recheck_pages=[1],
    ))
    assert report["error_counts"] == {"NistpCardError": 1}
    assert report["errors"] == [{
        "page": 1, "code": "NistpCardError",
        "external_id": "nistp.ru:501045:2",
        "stage": "detail",
        "reason": "requested_lot_missing_in_trade_card",
    }]
    assert report["detail_sample_pass"] is False


def test_cdtrf_failure_report_identifies_exact_lot_stage_and_safe_reason() -> None:
    from bankrotai.connectors.registry.cdtrf_public import CdtrfEvidenceError

    def broken_detail(trade, lot, session):
        raise CdtrfEvidenceError("Price disagreement between HTML and SSR")

    report = qa.check_cdtrf_details(
        1, 1, session=object(), sleep=lambda seconds: None,
        fetch_page=_cdtrf_page, fetch_detail=broken_detail, recheck_pages=[1],
    )
    assert report["errors"] == [{
        "page": 1, "code": "CdtrfEvidenceError",
        "external_id": "cdtrf:390899",
        "stage": "detail",
        "reason": "html_ssr_initial_price_mismatch",
    }]
    assert report["detail_sample_pass"] is False


def test_pinned_failure_ids_are_validated_and_rejected_if_missing(tmp_path) -> None:
    import json

    file = tmp_path / "prior.json"
    base = {
        "source": "cdtrf.ru",
        "mode": "bounded_independent_readonly_lot_detail_sample",
        "last_page_from_previous_full_walk": 64,
        "source_accepted": False,
        "errors": [{
            "page": 9, "external_id": "cdtrf:390579",
            "stage": "detail", "code": "CdtrfEvidenceError",
        }],
    }
    file.write_text(json.dumps(base), encoding="utf-8")
    assert qa.pinned_failed_targets(file, source="cdtrf.ru", last_page=64) == {
        9: "cdtrf:390579",
    }
    del base["errors"][0]["external_id"]
    file.write_text(json.dumps(base), encoding="utf-8")
    with pytest.raises(ValueError, match="trustworthy exact lot IDs"):
        qa.pinned_failed_targets(file, source="cdtrf.ru", last_page=64)


def test_pinned_cdtrf_recheck_does_not_substitute_first_lot_on_changed_page() -> None:
    report = qa.check_cdtrf_details(
        1, 1, session=object(), sleep=lambda seconds: None,
        recheck_pages=[1], pinned_targets={1: "cdtrf:999999"},
        fetch_page=_cdtrf_page,
        fetch_detail=lambda trade, lot, session: pytest.fail(
            "Must not request detail for another lot"
        ),
    )
    assert report["sample_attempted"] == 1
    assert report["sample_verified"] == 0
    assert report["detail_sample_pass"] is False
    assert report["exact_original_ids_pinned"] is True
    assert report["errors"] == [{
        "page": 1, "code": "pinned_lot_not_in_active_catalogue_page",
        "external_id": "cdtrf:999999", "stage": "catalogue",
    }]


def test_pinned_nistp_recheck_does_not_replace_missing_target(monkeypatch) -> None:
    monkeypatch.setattr(
        qa, "parse_nistp_lot_observations",
        lambda *args, **kwargs: [_nistp_observation()],
    )
    report = asyncio.run(qa.check_nistp_details(
        1, 1, connector_factory=NistpStub, sleep=lambda seconds: None,
        recheck_pages=[1], pinned_targets={1: "nistp.ru:502379:139"},
    ))
    assert report["sample_attempted"] == 1
    assert report["sample_verified"] == 0
    assert report["detail_sample_pass"] is False
    assert report["errors"] == [{
        "page": 1, "code": "pinned_lot_not_in_active_catalogue_page",
        "external_id": "nistp.ru:502379:139", "stage": "catalogue",
    }]


def test_cdtrf_lot_title_difference_is_audited_not_hidden() -> None:
    def divergent_title(trade, lot, session):
        detail = _detail("cdtrf.ru", f"cdtrf:{lot}", price=200.00, status="scheduled")
        detail.raw_data.update({
            "html_title_matches_nuxt_lot_name": False,
            "source_title_disagreement_review_required": True,
        })
        return detail

    report = qa.check_cdtrf_details(
        1, 1, session=object(), sleep=lambda seconds: None,
        fetch_page=_cdtrf_page, fetch_detail=divergent_title,
    )
    assert report["sample_verified"] == 1
    assert report["source_title_disagreement_review_count"] == 1
    assert report["source_accepted"] is False
    assert report["observations"][0]["html_title_matches_nuxt_lot_name"] is False
    assert report["observations"][0]["source_title_disagreement_review_required"] is True


def test_cdtrf_missing_pinned_lot_emits_immediate_progress_line(capsys) -> None:
    report = qa.check_cdtrf_details(
        1, 1, session=object(), sleep=lambda seconds: None,
        recheck_pages=[1], pinned_targets={1: "cdtrf:999999"},
        fetch_page=_cdtrf_page,
        fetch_detail=lambda trade, lot, session: pytest.fail("unexpected detail call"),
    )
    output = capsys.readouterr().err
    assert "CDTRF detail QA: page=1" in output
    assert "outcome=pinned_lot_not_in_active_catalogue_page" in output
    assert "elapsed_s=" in output
    assert report["detail_sample_pass"] is False


def test_nistp_missing_pinned_lot_emits_immediate_progress_line(capsys, monkeypatch) -> None:
    monkeypatch.setattr(
        qa, "parse_nistp_lot_observations",
        lambda *args, **kwargs: [_nistp_observation()],
    )
    report = asyncio.run(qa.check_nistp_details(
        1, 1, connector_factory=NistpStub, sleep=lambda seconds: None,
        recheck_pages=[1], pinned_targets={1: "nistp.ru:502379:139"},
    ))
    output = capsys.readouterr().err
    assert "NISTP detail QA: page=1" in output
    assert "outcome=pinned_lot_not_in_active_catalogue_page" in output
    assert "elapsed_s=" in output
    assert report["detail_sample_pass"] is False


def test_cdtrf_access_refusal_still_emits_progress(capsys) -> None:
    class Denied:
        def get(self, url, **kwargs):
            return SimpleNamespace(status_code=429, content=b"", headers={})

    report = qa.check_cdtrf_details(
        2, 2, session=Denied(), sleep=lambda seconds: None,
    )
    stderr = capsys.readouterr().err
    assert stderr.count("CDTRF detail QA: page=") == 1
    assert "outcome=SourceAccessError" in stderr
    assert report["detail_sample_pass"] is False
