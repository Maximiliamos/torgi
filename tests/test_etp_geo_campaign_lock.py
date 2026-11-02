"""Operator GEO campaigns wait for live worker locks, never fabricate success."""
from __future__ import annotations

from contextlib import contextmanager

import pytest


class ScalarResult:
    def all(self):
        return [123]


class Session:
    def scalars(self, _statement):
        return ScalarResult()


@contextmanager
def session_factory():
    yield Session()


def test_cdtrf_geo_waits_for_distributed_worker_lock(monkeypatch):
    from bankrotai.services import etp_cdtrf_campaign as campaign
    from bankrotai.services import geo_backfill

    attempts = []

    def geocode(*_args, **_kwargs):
        attempts.append(True)
        if len(attempts) == 1:
            raise geo_backfill.GeoBatchAlreadyRunning("other active batch")
        return {"status": "completed", "queued": 1, "geocoded": 1, "failed": 0}

    monkeypatch.setattr(campaign, "SessionLocal", session_factory)
    monkeypatch.setattr(campaign, "snapshot", lambda: {"without_coordinates": 0})
    monkeypatch.setattr(campaign.time, "sleep", lambda _: None)
    monkeypatch.setattr(geo_backfill, "geocode_pending_lots", geocode)

    result = campaign.geocode_source(batch_size=1)
    assert len(attempts) == 2
    assert result["requested"] == 1
    assert result["batches"][0]["geocoded"] == 1
    assert result["scope"] == "missing_coordinates_with_cadastre_or_address"


def test_cdtrf_geo_fails_closed_when_paused(monkeypatch):
    from bankrotai.services import etp_cdtrf_campaign as campaign
    from bankrotai.services import geo_backfill

    monkeypatch.setattr(campaign, "SessionLocal", session_factory)
    monkeypatch.setattr(
        geo_backfill, "geocode_pending_lots",
        lambda *_args, **_kwargs: {"status": "paused", "queued": 0},
    )
    with pytest.raises(RuntimeError, match="not completed: paused"):
        campaign.geocode_source(batch_size=1)
