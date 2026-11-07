from __future__ import annotations

import asyncio
from types import SimpleNamespace

from bankrotai.services import etp_parser_checklist as checklist


def _lot(source: str, ident: str = "lot-1", **overrides):
    data = dict(
        external_id=ident,
        source_system=source,
        start_price=100_000.0,
        current_price=None,
        auction_status="active",
        category="land",
        detail_level="search",
    )
    data.update(overrides)
    return SimpleNamespace(**data)


class FakeConnector:
    capabilities = frozenset({"search", "fetch_lot"})

    def __init__(self, source: str):
        self.source = source
        self.calls = 0

    async def search(self, _filters, _cursor=None):
        self.calls += 1
        return SimpleNamespace(
            items=[_lot(self.source, f"lot-{self.calls}")],
            next_cursor=None,
            metadata={},
        )

    async def fetch_lot(self, external_id: str):
        return _lot(self.source, external_id, detail_level="detail")



def test_missing_detail_capability_is_not_false_failure(monkeypatch):
    class ListingOnly(FakeConnector):
        capabilities = frozenset({"search"})

    monkeypatch.setattr(
        checklist, "_connector", lambda source: (ListingOnly(source), object())
    )
    result = asyncio.run(checklist.diagnose_source("etpu.ru"))
    assert result["stages"]["individual_detail"]["state"] == checklist.NOT_APPLICABLE
    assert result["stages"]["detail_identity"]["state"] == checklist.NOT_APPLICABLE
    assert result["first_failure"] is None



def test_detail_identity_mismatch_never_counts_as_pass(monkeypatch):
    class WrongIdentity(FakeConnector):
        async def fetch_lot(self, _external_id: str):
            return _lot(self.source, "wrong-id", detail_level="detail")

    monkeypatch.setattr(
        checklist, "_connector", lambda source: (WrongIdentity(source), object())
    )
    result = asyncio.run(checklist.diagnose_source("etpu.ru"))
    assert result["stages"]["detail_identity"]["state"] == checklist.FAIL
    assert result["stages"]["individual_detail"]["state"] != checklist.PASS
    assert result["first_failure"]["stage"] == "detail_identity"



def test_terminal_page_with_next_cursor_fails_closed(monkeypatch):
    class Contradiction(FakeConnector):
        async def search(self, _filters, _cursor=None):
            return SimpleNamespace(
                items=[_lot(self.source)],
                next_cursor="2",
                metadata={"source_end_verified": True},
            )

    monkeypatch.setattr(
        checklist, "_connector", lambda source: (Contradiction(source), object())
    )
    result = asyncio.run(checklist.diagnose_source("etpu.ru"))
    assert result["stages"]["last_page_proven"]["state"] == checklist.FAIL
    assert result["first_failure"]["category"] == "terminal_page_has_next_cursor"



def test_partial_detail_success_is_never_green(monkeypatch):
    class PartiallyBroken(FakeConnector):
        async def search(self, _filters, _cursor=None):
            return SimpleNamespace(
                items=[_lot(self.source, "lot-1"), _lot(self.source, "lot-2")],
                next_cursor=None,
                metadata={},
            )

        async def fetch_lot(self, external_id: str):
            if external_id == "lot-2":
                raise ValueError("broken detail")
            return _lot(self.source, external_id, detail_level="detail")

    monkeypatch.setattr(
        checklist, "_connector", lambda source: (PartiallyBroken(source), object())
    )
    result = asyncio.run(checklist.diagnose_source("etpu.ru", detail_probes=2))
    assert result["stages"]["individual_detail"]["state"] == checklist.FAIL
    assert result["stages"]["individual_detail_price"]["state"] == checklist.NOT_TESTED
    assert result["stages"]["individual_detail_status"]["state"] == checklist.NOT_TESTED
