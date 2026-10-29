"""Security gates for manual ETP canary import — no real network or DB."""
import pytest

from bankrotai.services import etp_manual_import


def test_manual_write_rejected_without_both_independent_gates(monkeypatch):
    monkeypatch.delenv("ETP_OPERATOR_CANARY_IMPORT_ENABLED", raising=False)
    monkeypatch.delenv("ETP_CANARY_BACKUP_VERIFIED", raising=False)
    monkeypatch.setattr(etp_manual_import, "collect_lots", lambda *a: pytest.fail("network attempted"))
    with pytest.raises(PermissionError, match="operator and verified-backup"):
        etp_manual_import.run_import("m-ets.ru", limit=1, apply=True)
    monkeypatch.setenv("ETP_OPERATOR_CANARY_IMPORT_ENABLED", "1")
    with pytest.raises(PermissionError, match="operator and verified-backup"):
        etp_manual_import.run_import("m-ets.ru", limit=1, apply=True)


def test_invalid_manual_source_and_limit_rejected_before_network():
    import asyncio
    with pytest.raises(ValueError):
        asyncio.run(etp_manual_import.collect_lots("rts-tender.ru", 1))
    with pytest.raises(ValueError):
        asyncio.run(etp_manual_import.collect_lots("m-ets.ru", 30))


def test_manual_import_refuses_empty_verified_batch(monkeypatch):
    monkeypatch.setenv("ETP_OPERATOR_CANARY_IMPORT_ENABLED", "1")
    monkeypatch.setenv("ETP_CANARY_BACKUP_VERIFIED", "1")
    async def empty(source, limit):
        return []
    monkeypatch.setattr(etp_manual_import, "collect_lots", empty)
    with pytest.raises(RuntimeError, match="No verified"):
        etp_manual_import.run_import("cdtrf.ru", limit=1, apply=True)
