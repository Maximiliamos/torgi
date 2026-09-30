from __future__ import annotations

import socket

import requests

from bankrotai.services import geo_resilience


def _reset(monkeypatch) -> None:
    geo_resilience._PROVIDER_STATE.clear()
    geo_resilience._PROVIDER_REFRESHED_AT.clear()
    geo_resilience._NETWORK_STATE.clear()
    geo_resilience._NETWORK_REFRESHED_AT = 0.0
    geo_resilience._REDIS_DISABLED_UNTIL = 0.0
    monkeypatch.setattr(geo_resilience, "_read_redis_json", lambda _key: None)
    monkeypatch.setattr(geo_resilience, "_write_redis_json", lambda _key, _value: None)


def test_operational_exception_classification_distinguishes_dns_timeout_and_tls() -> None:
    assert geo_resilience.classify_operational_exception(socket.gaierror("dns")) == "dns_error"
    assert geo_resilience.classify_operational_exception(requests.exceptions.ConnectTimeout()) == "connect_timeout"
    assert geo_resilience.classify_operational_exception(requests.exceptions.ReadTimeout()) == "read_timeout"
    assert geo_resilience.classify_operational_exception(requests.exceptions.SSLError("certificate")) == "tls_error"
    assert geo_resilience.is_operational_reason("operational:dns_error") is True
    assert geo_resilience.is_operational_reason("no_coordinates") is False


def test_provider_circuit_opens_after_repeated_operational_failure_and_recovers(monkeypatch) -> None:
    _reset(monkeypatch)

    first = geo_resilience.record_provider_failure("nspd", "read_timeout")
    second = geo_resilience.record_provider_failure("nspd", "read_timeout")

    assert first["state"] == "degraded"
    assert second["state"] == "open"
    assert geo_resilience.provider_available("nspd") is False

    recovered = geo_resilience.record_provider_success("nspd", latency_ms=42)
    assert recovered["state"] == "healthy"
    assert recovered["last_latency_ms"] == 42.0
    assert geo_resilience.provider_available("nspd") is True


def test_network_probe_detects_correlated_external_failure_and_requires_stable_recovery(monkeypatch) -> None:
    _reset(monkeypatch)
    monkeypatch.setattr(geo_resilience, "_probe_photon", lambda: (True, None, 10.0))
    monkeypatch.setattr(geo_resilience, "runtime_network_fingerprint", lambda: "profile-a")

    def degraded(host: str, port: int = 443, timeout: float = 3.0):
        if host in {"nspd.gov.ru", "api.roscadastres.com"}:
            return False, "dns_error", 100.0
        return True, None, 20.0

    monkeypatch.setattr(geo_resilience, "_probe_tcp_tls", degraded)
    down = geo_resilience.probe_geo_network()

    assert down["state"] == "down"
    assert down["failed_external_probes"] == 2
    assert geo_resilience.external_network_available() is False

    monkeypatch.setattr(
        geo_resilience,
        "_probe_tcp_tls",
        lambda _host, port=443, timeout=3.0: (True, None, 15.0),
    )
    monkeypatch.setattr(geo_resilience, "runtime_network_fingerprint", lambda: "profile-b")
    recovering = geo_resilience.probe_geo_network()
    healthy = geo_resilience.probe_geo_network()

    assert recovering["state"] == "recovering"
    assert recovering["fingerprint_changed"] is True
    assert healthy["state"] == "healthy"
    assert healthy["network_recovered"] is True
    assert geo_resilience.external_network_available() is True


def test_resilience_snapshot_does_not_expose_network_addresses(monkeypatch) -> None:
    _reset(monkeypatch)
    geo_resilience.record_provider_failure("photon", "local_service_unavailable", detail="private detail")

    snapshot = geo_resilience.resilience_snapshot()

    assert "providers" in snapshot
    assert snapshot["providers"]["photon"]["last_error_category"] == "local_service_unavailable"
    assert "provider_results" not in snapshot["providers"]["photon"]
    assert "private detail" not in str(snapshot)
    assert snapshot["providers"]["photon"]["last_error_fingerprint"] is not None
