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


def test_cdtrf_read_only_canary_probes_five_details_per_page_but_selects_only_two(monkeypatch):
    import asyncio
    import hashlib
    from types import SimpleNamespace

    from bankrotai.connectors.base import ConnectorPage

    calls = []

    class Connector:
        async def search(self, filters, cursor=None):
            calls.append((filters.max_detail_requests, filters.max_pages, cursor))
            page = len(calls)
            assert page in (1, 2)
            ids = [f"trade-{page}-lot-{i}" for i in range(1, 6)]
            fingerprints = [hashlib.sha256(v.encode("utf-8")).hexdigest() for v in ids]
            count = 1 if page == 1 else 2
            lots = [
                SimpleNamespace(
                    external_id=f"cdtrf-{page}-{i}",
                    source_system="cdtrf.ru",
                    category="land",
                    cadastral_number=f"50:05:0010203:{100 + 10 * page + i}",
                    start_price=1000000.0,
                    current_price=None,
                    auction_status="active",
                    lot_url=f"https://cdtrf.ru/lot/{page}/{i}",
                )
                for i in range(count)
            ]
            return ConnectorPage(
                items=lots,
                next_cursor=f"{page + 1}:test" if page < 5 else None,
                metadata={
                    "complete_source_inventory": False,
                    "safe_for_archival": False,
                    "progress_current": page,
                    "progress_total": 5,
                    "catalogue_lots": len(ids),
                    "catalogue_identity_fingerprints": fingerprints,
                },
            )

    monkeypatch.setattr(etp_manual_import.connector_registry, "create", lambda source: Connector())
    selected = asyncio.run(etp_manual_import.collect_lots("cdtrf.ru", 2))
    assert [x.external_id for x in selected] == ["cdtrf-1-0", "cdtrf-2-0"]
    assert calls == [(5, 5, None), (5, 5, "2:test")]


def test_s5_reports_individual_source_readiness_without_automatic_full_sync():
    from pathlib import Path
    script = (Path(__file__).resolve().parents[1] / "scripts" / "etp-canary-readiness.ps1").read_text(
        encoding="utf-8"
    )
    assert "ready_for_source_scoped_canary" in script
    assert "eligible_source_ids" in script
    assert "blocked_source_ids" in script
    assert "ready_for_automatic_sync = $false" in script
    assert "$eligible.Count -ge 1" in script
    assert "all_source_details_preflight_passed" in script
    assert "Test-RecentRestoreVerifiedBackup" in script
    assert "--audit-collisions" in script
    assert "--source $source --limit 2 --audit-collisions" in script
