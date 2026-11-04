from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from bankrotai.connectors.base import ConnectorPage
from bankrotai.services.etp_two_pass_acceptance import audit_two_passes


def _lot(identity: str, source: str = "test.etp", *, detail: bool = True):
    return SimpleNamespace(
        external_id=identity, source_system=source,
        auction_status="active", start_price=100000.50,
        current_price=None, detail_level="detail" if detail else "search",
    )


class Source:
    capabilities = frozenset({"search", "fetch_lot", "detail_enrichment"})

    def __init__(self, identities=("a", "b"), *, terminal=True, duplicate=False,
                 bad_detail=False, unknown=False):
        self.identities = identities
        self.terminal = terminal
        self.duplicate = duplicate
        self.bad_detail = bad_detail
        self.unknown = unknown
        self.requested = []

    async def search(self, filters, cursor=None):
        self.requested.append(cursor)
        if cursor is None:
            return ConnectorPage(
                items=[_lot(self.identities[0])],
                next_cursor="page-2",
                metadata={"total_pages": 2, "progress_current": 1},
            )
        if cursor == "page-2":
            item = _lot(self.identities[0] if self.duplicate else self.identities[1])
            item.detail_level = "search"
            if self.unknown:
                item.auction_status = "unexpected_status"
            return ConnectorPage(
                items=[item], next_cursor=None,
                metadata={
                    "total_pages": 2 if self.terminal else None,
                    "progress_current": 2,
                },
            )
        raise AssertionError("unexpected cursor")

    async def fetch_lot(self, identity):
        return _lot("wrong" if self.bad_detail else identity)


def test_two_full_independent_passes_are_review_only_not_production_approval():
    created = []
    def factory():
        instance = Source()
        created.append(instance)
        return instance

    result = asyncio.run(audit_two_passes(
        "test.etp", factory, {}, max_pages=3, delay_seconds=0.5,
        max_detail_probes=2,
    ))
    assert len(created) == 2
    assert all(instance.requested == [None, "page-2"] for instance in created)
    assert result["two_complete_passes"] is True
    assert result["stable_identity_set"] is True
    assert result["detail_sample_verified"] is True
    assert result["status"] == "review_required"
    assert result["production_approved"] is False
    assert result["database_writes"] == 0
    assert result["map_writes"] == 0
    assert result["passes"][0]["identity_sha256"] == result["passes"][1]["identity_sha256"]
    assert result["passes"][0]["terminal_evidence"] == "declared_total_pages"


def test_catalogue_end_cannot_be_inferred_from_missing_next_cursor():
    result = asyncio.run(audit_two_passes(
        "test.etp", lambda: Source(terminal=False), {},
        max_pages=2, delay_seconds=0.5,
    ))
    assert result["status"] == "blocked"
    assert "last_page_not_independently_proven" in result["reasons"]
    assert result["production_approved"] is False


def test_two_passes_reject_catalogue_identity_drift():
    calls = 0
    def factory():
        nonlocal calls
        calls += 1
        return Source(identities=("a", "b" if calls == 1 else "c"))
    result = asyncio.run(audit_two_passes(
        "test.etp", factory, {}, max_pages=2,
    ))
    assert result["status"] == "blocked"
    assert "identity_set_changed_between_passes" in result["reasons"]


@pytest.mark.parametrize("option", [{"duplicate": True}, {"unknown": True}])
def test_duplicate_or_unrecognised_status_fails_closed(option):
    result = asyncio.run(audit_two_passes(
        "test.etp", lambda: Source(**option), {}, max_pages=2,
    ))
    assert result["status"] == "blocked"
    assert result["completed_passes"] == 0
    assert result["database_writes"] == 0


def test_bounded_scan_refuses_page_cap_before_last_page():
    result = asyncio.run(audit_two_passes(
        "test.etp", lambda: Source(), {}, max_pages=1,
    ))
    assert result["status"] == "blocked"
    assert result["completed_passes"] == 0


def test_individual_detail_identity_mismatch_is_never_accepted():
    result = asyncio.run(audit_two_passes(
        "test.etp", lambda: Source(bad_detail=True), {}, max_pages=2,
    ))
    assert result["status"] == "blocked"
    assert not result["detail_sample_verified"]
    assert result["passes"][0]["detail_errors"] == ["ValueError"]


@pytest.mark.parametrize("kwargs", [
    {"max_pages": 0}, {"max_pages": 3501},
    {"max_detail_probes": 31}, {"delay_seconds": 0.01},
])
def test_never_run_unbounded_or_aggressive_source_scan(kwargs):
    with pytest.raises(ValueError):
        asyncio.run(audit_two_passes("test.etp", Source, {}, **kwargs))
