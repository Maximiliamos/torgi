"""Regression gates for a two-lot, provenance-verified ETP operator canary.

These tests have no network or production DB dependencies.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from bankrotai.services.etp_manual_import import (
    collect_lots, run_import, verified_canary_items,
)


def sale(**overrides):
    fields = {
        "source_system": "m-ets.ru",
        "external_id": "243003-1",
        "category": "land",
        "cadastral_number": "76:23:010101:1",
        "auction_status": "active",
        "current_price": 500_000,
        "start_price": None,
        "lot_url": "https://m-ets.ru/lot/243003-1",
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


def page(items, **metadata_overrides):
    metadata = {"complete_source_inventory": False, "safe_for_archival": False}
    metadata.update(metadata_overrides)
    return SimpleNamespace(items=items, metadata=metadata)


def test_exactly_two_valid_lots_are_allowed():
    accepted = [sale(), sale(external_id="243003-2")]
    assert verified_canary_items(page(accepted), source="m-ets.ru", limit=2) == accepted


def test_more_than_two_items_fail_closed_not_silently_truncated():
    rows = [sale(external_id=f"lot-{n}") for n in range(3)]
    with pytest.raises(ValueError, match="exceeded"):
        verified_canary_items(page(rows), source="m-ets.ru", limit=2)


@pytest.mark.parametrize("metadata", [
    {"complete_source_inventory": True},
    {"safe_for_archival": True},
    {"complete_source_inventory": None},
    {"safe_for_archival": None},
])
def test_invalid_non_archival_metadata_is_rejected(metadata):
    with pytest.raises(ValueError, match="non-archival"):
        verified_canary_items(page([sale()], **metadata), source="m-ets.ru", limit=2)


def test_missing_metadata_is_rejected():
    with pytest.raises(ValueError, match="non-archival"):
        verified_canary_items(
            SimpleNamespace(items=[sale()], metadata={}),
            source="m-ets.ru", limit=2,
        )


@pytest.mark.parametrize("rows", [
    [sale(source_system="cdtrf.ru")],
    [sale(external_id="")],
    [sale(), sale()],
    [sale(category="vehicle")],
    [sale(auction_status="closed")],
    [sale(cadastral_number=None)],
    [sale(lot_url=None)],
    [sale(current_price=None, start_price=None)],
])
def test_unverified_or_duplicate_lots_are_rejected(rows):
    with pytest.raises(ValueError, match="Unverified"):
        verified_canary_items(page(rows), source="m-ets.ru", limit=2)


def test_limit_greater_than_two_refused_before_creating_connector(monkeypatch):
    from bankrotai.services import etp_manual_import as module

    monkeypatch.setattr(
        module.connector_registry, "create",
        lambda *args: pytest.fail("must not access connector"),
    )
    with pytest.raises(ValueError, match="canary limit"):
        asyncio.run(collect_lots("m-ets.ru", 3))


def test_apply_without_operator_and_backup_refused_before_network(monkeypatch):
    from bankrotai.services import etp_manual_import as module

    monkeypatch.delenv("ETP_OPERATOR_CANARY_IMPORT_ENABLED", raising=False)
    monkeypatch.delenv("ETP_CANARY_BACKUP_VERIFIED", raising=False)
    monkeypatch.setattr(
        module.connector_registry, "create",
        lambda *args: pytest.fail("must not access connector"),
    )
    with pytest.raises(PermissionError, match="verified-backup"):
        run_import("m-ets.ru", limit=2, apply=True)


def test_workflow_checks_accepted_deploy_twice_before_mutation():
    root = Path(__file__).resolve().parents[1]
    workflow = (root / ".github/workflows/etp-manual-verified-import.yml").read_text(
        encoding="utf-8"
    )
    guard = (root / "scripts/verify-etp-canary-acceptance.ps1").read_text(
        encoding="utf-8"
    )
    assert workflow.count("verify-etp-canary-acceptance.ps1 -ExpectedSha") == 2
    assert workflow.index("verify-etp-canary-acceptance.ps1") < workflow.index(
        "Require recent locally restore-verified backup"
    )
    assert workflow.rindex("verify-etp-canary-acceptance.ps1") < workflow.index(
        "python -m bankrotai.services.etp_manual_import"
    )
    assert "actions: read" in workflow
    assert "github.ref == 'refs/heads/main'" in workflow
    for fragment in (
        "branches/main", "P11 production acceptance",
        "Deploy home secondary origin", "Deploy WEB and API to REG.RU",
        "Deploy Cloudflare edge proxy", "Production functional reliability",
        "Public WEB smoke", "P1 production data quality",
        "bankrotai-home-api:$ExpectedSha",
    ):
        assert fragment in guard


def test_two_verified_lots_commit_once_with_public_source_ids(monkeypatch):
    from contextlib import contextmanager
    from bankrotai import db
    from bankrotai.services import etp_canary_persistence as persistence
    from bankrotai.services import etp_manual_import as module

    rows = [sale(), sale(external_id="243003-2")]
    async def candidates(source, limit):
        return rows
    monkeypatch.setattr(module, "collect_lots", candidates)
    monkeypatch.setenv("ETP_OPERATOR_CANARY_IMPORT_ENABLED", "1")
    monkeypatch.setenv("ETP_CANARY_BACKUP_VERIFIED", "1")
    transactions = []
    @contextmanager
    def isolated():
        proposed = []
        try:
            yield proposed
            transactions.append(list(proposed))
        except Exception:
            proposed.clear()
            raise
    def persist(sess, item, actor):
        sess.append(item.external_id)
    monkeypatch.setattr(db, "session_scope", isolated)
    monkeypatch.setattr(persistence, "persist_canary_lot", persist)

    outcome = run_import("m-ets.ru", limit=2, apply=True)
    assert transactions == [["243003-1", "243003-2"]]
    assert outcome["persisted_count"] == 2
    assert outcome["persisted_external_ids"] == ["243003-1", "243003-2"]
    assert outcome["missing_lot_archival"] is False


def test_second_lot_collision_rolls_back_first_and_fails_closed(monkeypatch):
    from contextlib import contextmanager
    from bankrotai import db
    from bankrotai.services import etp_canary_persistence as persistence
    from bankrotai.services import etp_manual_import as module

    rows = [sale(), sale(external_id="243003-2")]
    async def candidates(source, limit):
        return rows
    monkeypatch.setattr(module, "collect_lots", candidates)
    monkeypatch.setenv("ETP_OPERATOR_CANARY_IMPORT_ENABLED", "1")
    monkeypatch.setenv("ETP_CANARY_BACKUP_VERIFIED", "1")
    committed = []
    rolled_back = []
    @contextmanager
    def isolated():
        proposed = []
        try:
            yield proposed
            committed.extend(proposed)
        except Exception:
            rolled_back.extend(proposed)
            raise
    def persist(sess, item, actor):
        if item.external_id == "243003-2":
            raise persistence.CanaryDuplicateReviewRequired("manual review required")
        sess.append(item.external_id)
    monkeypatch.setattr(db, "session_scope", isolated)
    monkeypatch.setattr(persistence, "persist_canary_lot", persist)

    with pytest.raises(persistence.CanaryDuplicateReviewRequired):
        run_import("m-ets.ru", limit=2, apply=True)
    assert committed == []
    assert rolled_back == ["243003-1"]


def test_run_import_rejects_excessive_lots_even_if_collector_is_bypassed(monkeypatch):
    from bankrotai.services import etp_manual_import as module
    async def unexpected(source, limit):
        return [sale(external_id=f"other-{n}") for n in range(3)]
    monkeypatch.setattr(module, "collect_lots", unexpected)
    monkeypatch.setenv("ETP_OPERATOR_CANARY_IMPORT_ENABLED", "1")
    monkeypatch.setenv("ETP_CANARY_BACKUP_VERIFIED", "1")
    with pytest.raises(ValueError, match="unbounded"):
        run_import("m-ets.ru", limit=2, apply=True)
