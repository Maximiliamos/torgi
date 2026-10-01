from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import requests
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from bankrotai.core import utc_now
from bankrotai.db import Base, GeoFailure, ProcessedLot
from bankrotai.geo import CADASTRAL_GEOCODER, CadastralObjectResult, NOMINATIM_GEOCODER, PHOTON_GEOCODER
from bankrotai.services import geo_backfill, geo_resilience
from bankrotai.services.geo_resilience import (
    GeoProviderUnavailable,
    classify_transport_exception,
    retry_delay_seconds,
)


def _lot() -> ProcessedLot:
    return ProcessedLot(
        external_id="p6-geo",
        source="test",
        source_system="test",
        title="Участок",
        description="",
        category="land",
        address="Ярославль, улица Свободы, 1",
        auction_status="active",
    )


def test_transport_errors_are_classified_separately_from_no_match() -> None:
    assert classify_transport_exception(requests.ConnectTimeout()) == "connect_timeout"
    assert classify_transport_exception(requests.ReadTimeout()) == "read_timeout"
    assert classify_transport_exception(
        requests.ConnectionError("Failed to resolve host: NameResolutionError")
    ) == "dns_error"
    assert classify_transport_exception(requests.exceptions.SSLError("certificate")) == "tls_error"


def test_p6_retry_policy_has_no_multiday_operational_backoff() -> None:
    assert [retry_delay_seconds("read_timeout", attempt) for attempt in range(1, 6)] == [
        60,
        180,
        300,
        900,
        900,
    ]
    assert retry_delay_seconds("no_match", 1) == 1800
    assert retry_delay_seconds("no_match", 2) == 7200
    assert retry_delay_seconds("no_match", 3) is None
    assert retry_delay_seconds("validation", 1) == 300
    assert retry_delay_seconds("validation", 2) == 1800
    assert retry_delay_seconds("validation", 3) is None


def test_operational_failure_does_not_consume_semantic_retry_budget() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        lot = _lot()
        session.add(lot)
        session.flush()
        session.add(
            GeoFailure(
                lot_id=lot.id,
                status="queued",
                attempt_count=5,
                error_message='{"error":"old no match","attempts":[]}',
                last_failed_at=utc_now(),
                next_retry_at=utc_now(),
            )
        )
        session.flush()

        label = geo_backfill._record_classified_failure(
            session,
            lot.id,
            GeoProviderUnavailable("nspd", "read_timeout"),
        )
        failure = session.scalar(select(GeoFailure).where(GeoFailure.lot_id == lot.id))

        assert failure is not None
        assert label == "operational:read_timeout"
        assert failure.status == "network_wait"
        assert failure.attempt_count == 5
        assert failure.next_retry_at is not None
        next_retry = failure.next_retry_at
        assert next_retry is not None
        assert next_retry.replace(tzinfo=None) <= utc_now().replace(tzinfo=None) + timedelta(minutes=2)


def test_repeated_no_match_is_deferred_in_hours_not_days() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    miss = CadastralObjectResult(
        query="x",
        source="geocoding_chain",
        confidence="none",
        status="GEOCODING_FAILED",
        error="No validated geocoding result",
        attempts=[{"source": "address_geocoder", "valid": False, "reason": "no_coordinates"}],
    )
    with Session(engine) as session:
        lot = _lot()
        session.add(lot)
        session.flush()

        assert geo_backfill._record_classified_failure(session, lot.id, miss) == "no_match"
        failure = session.scalar(select(GeoFailure).where(GeoFailure.lot_id == lot.id))
        assert failure is not None
        assert failure.attempt_count == 1
        assert failure.status == "queued"

        geo_backfill._record_classified_failure(session, lot.id, miss)
        assert failure.attempt_count == 2
        assert failure.status == "queued"

        geo_backfill._record_classified_failure(session, lot.id, miss)
        assert failure.attempt_count == 3
        assert failure.status == "deferred_no_match"
        assert failure.next_retry_at is None


def test_changed_geo_input_reactivates_deferred_lot() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    miss = CadastralObjectResult(
        query="x",
        source="geocoding_chain",
        confidence="none",
        status="GEOCODING_FAILED",
        error="No validated geocoding result",
        attempts=[{"source": "address_geocoder", "valid": False, "reason": "no_coordinates"}],
    )
    with Session(engine) as session:
        lot = _lot()
        lot.geo_input_hash = None
        session.add(lot)
        session.flush()
        session.add(
            GeoFailure(
                lot_id=lot.id,
                status="deferred_no_match",
                attempt_count=7,
                error_message='{"classification":"no_match"}',
                last_failed_at=utc_now(),
                next_retry_at=None,
            )
        )
        session.flush()

        ok, label = geo_backfill._save_geo_item(session, lot, miss)
        failure = session.scalar(select(GeoFailure).where(GeoFailure.lot_id == lot.id))

        assert not ok
        assert label == "address_geocoder:no_coordinates"
        assert failure is not None
        assert failure.attempt_count == 1
        assert failure.status == "queued"
        assert failure.next_retry_at is not None


def test_photon_outage_fails_over_to_nominatim(monkeypatch) -> None:
    monkeypatch.setattr(
        PHOTON_GEOCODER,
        "geocode",
        lambda _address: (_ for _ in ()).throw(GeoProviderUnavailable("photon", "connection_error")),
    )
    monkeypatch.setattr(
        NOMINATIM_GEOCODER,
        "geocode",
        lambda _address: {
            "centroid_lat": 57.6261,
            "centroid_lon": 39.8845,
            "geo_confidence": "medium",
            "matched_address": "Ярославль",
            "trace_reason": "test fallback",
        },
    )

    result = CADASTRAL_GEOCODER.search_by_address("Ярославль, улица Свободы, 1", allow_nominatim=True)

    assert result.source == "nominatim"
    assert result.lat == 57.6261


def test_deferred_lots_are_not_counted_as_runnable_backlog(monkeypatch) -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    monkeypatch.setattr(geo_backfill, "network_health_snapshot", lambda: {"external": {"circuit_open": False}})
    with Session(engine) as session:
        deferred = _lot()
        deferred.external_id = "p6-deferred"
        deferred.geo_input_hash = "a" * 64
        waiting = _lot()
        waiting.external_id = "p6-network-wait"
        session.add_all([deferred, waiting])
        session.flush()
        session.add_all([
            GeoFailure(
                lot_id=deferred.id,
                status="deferred_no_match",
                attempt_count=3,
                error_message='{"classification":"no_match"}',
                last_failed_at=utc_now(),
                next_retry_at=None,
            ),
            GeoFailure(
                lot_id=waiting.id,
                status="network_wait",
                attempt_count=0,
                error_message='{"classification":"operational"}',
                last_failed_at=utc_now(),
                next_retry_at=utc_now() + timedelta(minutes=5),
            ),
        ])
        session.commit()

        progress = geo_backfill.geocoding_progress(session)

        assert progress["deferred_no_match"] == 1
        assert progress["network_wait"] == 1
        assert progress["eligible_now"] == 0
        assert progress["waiting_for_retry"] == 1
        assert progress["actionable_remaining"] == 1
        assert progress["classified"] == 1


def test_p6_network_probe_runs_on_the_geocoding_queue_every_minute() -> None:
    tasks = (Path(__file__).resolve().parents[1] / "src" / "bankrotai" / "tasks.py").read_text(encoding="utf-8")

    assert '"bankrotai.tasks.probe_geo_network_health_task": {"queue": _QUEUE_GEOCODING}' in tasks
    assert '"probe-geo-network-health"' in tasks
    assert '"schedule": 60.0' in tasks


def test_global_circuit_counts_independent_dependencies_not_probe_aliases(monkeypatch) -> None:
    now = 1_000.0

    def state(provider: str) -> geo_resilience.ProviderHealth:
        if provider in {"nspd", "probe:nspd"}:
            return geo_resilience.ProviderHealth(
                provider=provider,
                consecutive_failures=3,
                last_category="read_timeout",
                last_failure_at=now - 5,
            )
        return geo_resilience.ProviderHealth(provider=provider)

    monkeypatch.setattr(geo_resilience, "provider_health", state)
    monkeypatch.setattr(geo_resilience, "_load_json", lambda _key: {})
    monkeypatch.setattr(geo_resilience, "_save_json", lambda *_args, **_kwargs: None)

    result = geo_resilience._refresh_global_circuit(now)

    assert result["degraded_providers"] == ["nspd"]
    assert result["circuit_open_until"] is None


def test_global_circuit_opens_for_two_independent_external_dependencies(monkeypatch) -> None:
    now = 1_000.0

    def state(provider: str) -> geo_resilience.ProviderHealth:
        if provider in {"nspd", "probe:nspd", "ik12", "probe:ik12"}:
            return geo_resilience.ProviderHealth(
                provider=provider,
                consecutive_failures=3,
                last_category="dns_error",
                last_failure_at=now - 5,
            )
        return geo_resilience.ProviderHealth(provider=provider)

    monkeypatch.setattr(geo_resilience, "provider_health", state)
    monkeypatch.setattr(geo_resilience, "_load_json", lambda _key: {})
    monkeypatch.setattr(geo_resilience, "_save_json", lambda *_args, **_kwargs: None)

    result = geo_resilience._refresh_global_circuit(now)

    assert result["degraded_providers"] == ["ik12", "nspd"]
    assert float(result["circuit_open_until"]) > now


def test_successful_external_probe_recovers_matching_real_provider(monkeypatch) -> None:
    recorded: list[str] = []

    monkeypatch.setattr(
        geo_resilience,
        "_probe_tls",
        lambda host, timeout=2.0: ("127.0.0.1", 10.0),
    )
    monkeypatch.setattr(
        geo_resilience,
        "record_provider_success",
        lambda provider, latency_ms=None: recorded.append(provider)
        or geo_resilience.ProviderHealth(provider=provider, last_success_at=1.0),
    )
    monkeypatch.setattr(
        geo_resilience,
        "record_provider_failure",
        lambda provider, category, latency_ms=None: geo_resilience.ProviderHealth(provider=provider),
    )
    monkeypatch.setattr(geo_resilience, "global_network_state", lambda: {"circuit_open": False})
    monkeypatch.setattr(geo_resilience, "_load_json", lambda _key: {})
    monkeypatch.setattr(geo_resilience, "_save_json", lambda *_args, **_kwargs: None)

    class Response:
        def raise_for_status(self) -> None:
            return None

    monkeypatch.setattr(geo_resilience.requests, "get", lambda *args, **kwargs: Response())
    monkeypatch.setenv("PHOTON_BASE_URL", "http://photon")

    geo_resilience.probe_geo_network_health()

    assert "probe:nspd" in recorded
    assert "nspd" in recorded
    assert "probe:ik12" in recorded
    assert "ik12" in recorded
    assert "photon" in recorded


def test_provider_recovery_requires_success_newer_than_lot_failure(monkeypatch) -> None:
    failed_at = utc_now()
    monkeypatch.setattr(geo_resilience, "provider_allowed", lambda provider, external: True)
    monkeypatch.setattr(
        geo_resilience,
        "provider_health",
        lambda provider: geo_resilience.ProviderHealth(
            provider=provider,
            last_success_at=failed_at.timestamp() + 1,
        ),
    )
    assert geo_resilience.provider_recovered_since("nspd", failed_at)

    monkeypatch.setattr(
        geo_resilience,
        "provider_health",
        lambda provider: geo_resilience.ProviderHealth(
            provider=provider,
            last_success_at=failed_at.timestamp() - 1,
        ),
    )
    assert not geo_resilience.provider_recovered_since("nspd", failed_at)
