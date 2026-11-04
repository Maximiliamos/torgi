"""Regression tests for safe recovery from stale Torgi Russia circuit state."""
from __future__ import annotations

from types import SimpleNamespace
from pathlib import Path

import pytest

from bankrotai.services import source_resilience, torgi_russia_health


class FakeClient:
    def __init__(self, *, lots=None, total=1250, pages=53, error=None):
        self._lots = lots
        self._total = total
        self._pages = pages
        self._error = error
        self.closed = False
        self.session = SimpleNamespace(close=lambda: setattr(self, "closed", True))
        self.filters = None

    def search_lots(self, filters):
        self.filters = filters
        if self._error:
            raise self._error
        return self._lots, {"total": self._total, "total_pages": self._pages}


def lot(lot_id: str = "torgi-russia:777", **kwargs):
    values = {
        "external_id": lot_id,
        "source_system": "torgi-russia.ru",
        "start_price": 350000.0,
        "auction_status": "active",
        "region_name": "Краснодарский край",
    }
    values.update(kwargs)
    return SimpleNamespace(**values)


def _substitute(monkeypatch, fake):
    from bankrotai import torgi_russia
    monkeypatch.setattr(torgi_russia, "TorgiRussiaClient", lambda *, timeout: fake)


def test_real_catalogue_proves_live_source_and_closes_session(monkeypatch):
    fake = FakeClient(lots=[lot(), lot("torgi-russia:778")])
    _substitute(monkeypatch, fake)
    result = torgi_russia_health.probe_torgi_russia_catalogue()
    assert result["success"] is True
    assert result["validated_lots"] == 2
    assert result["catalogue_total"] == 1250
    assert result["url"] == "https://xn----etbpba5admdlad.xn--p1ai/search"
    assert fake.filters.region_id == 23
    assert fake.filters.category_id == "6"
    assert fake.closed is True
    assert result["details_or_archive_verified"] is False


@pytest.mark.parametrize("lots,total,pages", [
    ([], 0, 0),
    ([lot(start_price=None)], 1, 1),
    ([lot(auction_status="unknown")], 1, 1),
    ([lot(region_name=None)], 1, 1),
    ([lot(source_system="wrong")], 1, 1),
    ([lot(), lot()], 2, 1),
    ([lot()], None, 1),
    ([lot()], 1, 0),
])
def test_never_close_circuit_on_empty_or_unproven_catalogue(
    monkeypatch, lots, total, pages
):
    fake = FakeClient(lots=lots, total=total, pages=pages)
    _substitute(monkeypatch, fake)
    result = torgi_russia_health.probe_torgi_russia_catalogue()
    assert result["success"] is False
    assert fake.closed is True


def test_dns_or_transport_error_keeps_circuit_closed_to_new_imports(monkeypatch):
    fake = FakeClient(error=ConnectionError("DNS failure with credential=secret"))
    _substitute(monkeypatch, fake)
    result = torgi_russia_health.probe_torgi_russia_catalogue()
    assert result["success"] is False
    assert result["error_type"] == "ConnectionError"
    assert "secret" not in result["error"]
    assert fake.closed is True


def test_circuit_probe_uses_actual_parser_and_not_alias_head(monkeypatch):
    called = []
    def stub(*, timeout_seconds):
        called.append(timeout_seconds)
        return {"success": True, "source_system": "torgi-russia.ru",
                "validated_lots": 24}
    monkeypatch.setattr(torgi_russia_health, "probe_torgi_russia_catalogue", stub)
    result = source_resilience.probe_source_endpoint(
        "torgi-russia.ru", timeout_seconds=7.5
    )
    assert called == [7.5]
    assert result["success"] is True
    assert source_resilience._SOURCE_PROBE_URLS["torgi-russia.ru"].endswith(
        "xn----etbpba5admdlad.xn--p1ai/search"
    )


def test_torgi_russia_full_automatic_source_must_not_archive_missing_historical_lots():
    from bankrotai.services.ingestion import default_source_specs, source_full_specs
    specs = [spec for spec in default_source_specs()
             if spec.source_id == "torgi-russia.ru"]
    assert len(specs) == 1
    assert specs[0].reconcile_missing is False
    assert specs[0].require_source_end is True
    assert source_full_specs("torgi-russia.ru")[0].reconcile_missing is False


def test_home_workflows_preserve_bounded_torgi_russia_failure_evidence():
    root = Path(__file__).resolve().parents[1] / ".github" / "workflows"
    audit = (root / "home-readonly-sync-map-audit.yml").read_text(encoding="utf-8")
    probe = (root / "torgi-russia-home-readonly-recovery.yml").read_text(
        encoding="utf-8"
    )
    assert "LotSyncSourceRun.error_message" in audit
    assert "safe_error(s.error_message)" in audit
    assert 'sample["error"] = error_detail(exc)' in probe
    assert 'str(exc).split())[:500]' in probe
