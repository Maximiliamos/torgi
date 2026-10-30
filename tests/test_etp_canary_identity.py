"""S5 preview identities must match the eventual source-ID write batch."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from bankrotai.services import etp_manual_import
from bankrotai.services.etp_canary_identity import canary_identity_digest


def sample(external_id):
    return SimpleNamespace(
        source_system="m-ets.ru", external_id=external_id,
        cadastral_number="76:23:010101:1", category="land",
        auction_status="active", current_price=100_000,
        start_price=None, lot_url="https://m-ets.ru/lot/" + external_id,
    )


def test_digest_is_source_scoped_and_independent_of_catalogue_order():
    one = sample("243003-1")
    two = sample("243003-2")
    first = canary_identity_digest("m-ets.ru", [one, two])
    assert len(first) == 64
    assert first == canary_identity_digest("m-ets.ru", [two, one])
    assert first != canary_identity_digest("cdtrf.ru", [one, two])
    assert "243003" not in first
    assert "76:23" not in first


@pytest.mark.parametrize("identities", [
    [], [sample("243003-1")] * 2,
    [sample("243003-1")] * 3,
    [sample("")], [sample("line" + chr(10) + "break")],
])
def test_digest_does_not_hash_missing_or_repeated_source_ids(identities):
    with pytest.raises(ValueError):
        canary_identity_digest("m-ets.ru", identities)


def test_apply_refuses_missing_or_invalid_digest_before_live_network(monkeypatch):
    monkeypatch.setenv("ETP_OPERATOR_CANARY_IMPORT_ENABLED", "1")
    monkeypatch.setenv("ETP_CANARY_BACKUP_VERIFIED", "1")

    async def should_not_fetch(source, limit):
        pytest.fail("Cannot fetch network before validating expected hash")
    monkeypatch.setattr(etp_manual_import, "collect_lots", should_not_fetch)
    for provided in (None, "", "a" * 63, "A" * 64, "a" * 65):
        with pytest.raises(PermissionError, match="exact reviewed identity digest"):
            etp_manual_import.run_import(
                "m-ets.ru", limit=2, apply=True,
                expected_identity_digest=provided,
            )


def test_source_id_changes_between_preview_and_apply_never_begin_transaction(monkeypatch):
    from bankrotai import db
    monkeypatch.setenv("ETP_OPERATOR_CANARY_IMPORT_ENABLED", "1")
    monkeypatch.setenv("ETP_CANARY_BACKUP_VERIFIED", "1")
    reviewed = [sample("243003-1"), sample("243003-2")]
    actual = [sample("243003-1"), sample("243003-3")]
    expected = canary_identity_digest("m-ets.ru", reviewed)

    async def changed(source, limit):
        return actual
    monkeypatch.setattr(etp_manual_import, "collect_lots", changed)
    monkeypatch.setattr(
        db, "session_scope",
        lambda: pytest.fail("No DB transaction is allowed after ID drift"),
    )
    with pytest.raises(ValueError, match="details changed since preview"):
        etp_manual_import.run_import(
            "m-ets.ru", limit=2, apply=True,
            expected_identity_digest=expected,
        )


def test_preview_exposes_only_hash_not_candidate_public_identity(monkeypatch):
    async def verified(source, limit):
        return [sample("243003-1")]
    monkeypatch.setattr(etp_manual_import, "collect_lots", verified)
    report = etp_manual_import.run_import("m-ets.ru", limit=1, apply=False)
    assert report["reviewed_identity_digest"] == canary_identity_digest(
        "m-ets.ru", [sample("243003-1")]
    )
    assert report["persisted_external_ids"] == []
    assert report["write_authorized"] is False
    assert "243003-1" not in str(report)
    assert "76:23:010101" not in str(report)


def test_manual_apply_keeps_operator_and_backup_gate_before_digest(monkeypatch):
    monkeypatch.delenv("ETP_OPERATOR_CANARY_IMPORT_ENABLED", raising=False)
    monkeypatch.delenv("ETP_CANARY_BACKUP_VERIFIED", raising=False)
    with pytest.raises(PermissionError, match="operator and verified-backup"):
        etp_manual_import.run_import(
            "m-ets.ru", limit=2, apply=True,
            expected_identity_digest="0" * 64,
        )
