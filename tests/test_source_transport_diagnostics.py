"""Direct transport checks must preserve TLS verification and scrub secrets."""
from __future__ import annotations

import socket
import ssl

import pytest

from scripts.source_transport_diagnostics import (
    CORE_SOURCE_HOSTS, _error_category, inspect_transport, survey,
)


class _Connection:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class _TLS(_Connection):
    def version(self):
        return "TLSv1.3"


class _Context:
    def wrap_socket(self, _connection, *, server_hostname):
        assert server_hostname == CORE_SOURCE_HOSTS[0]
        return _TLS()


def test_direct_dns_tls_success_never_prints_provider_address_or_secret():
    output = inspect_transport(
        CORE_SOURCE_HOSTS[0],
        resolver=lambda *_args, **_kwargs: [("ip", "ignored", "private-ip")],
        connector=lambda *_args, **_kwargs: _Connection(),
        context_factory=lambda: _Context(),
    )
    assert output == {
        "source": CORE_SOURCE_HOSTS[0],
        "dns": "ok",
        "tls": "ok",
        "tls_version": "TLSv1.3",
    }
    assert "private-ip" not in str(output)


def test_certificate_error_is_not_ignored_or_retried_without_verification():
    def reject(*_args, **_kwargs):
        raise ssl.SSLCertVerificationError("certificate rejected")

    output = inspect_transport(
        CORE_SOURCE_HOSTS[0],
        resolver=lambda *_args, **_kwargs: [object()],
        connector=reject,
        context_factory=lambda: _Context(),
    )
    assert output["tls"] == "failed"
    assert output["error_category"] == "tls_certificate"


def test_dns_error_is_distinct_from_tls_failure():
    def fail(*_args, **_kwargs):
        raise socket.gaierror("DNS unavailable")

    output = inspect_transport(CORE_SOURCE_HOSTS[0], resolver=fail)
    assert output["dns"] == "failed"
    assert output["tls"] == "not_checked"
    assert output["error_category"] == "dns_resolution"


def test_proxy_env_reports_boolean_never_credentials(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "https://user:secret@private.proxy.test")
    report = survey(inspector=lambda host, *, timeout: {
        "source": host, "dns": "failed", "tls": "not_checked",
    })
    assert report["proxy_environment_present"] is True
    assert report["tls_verification_disabled"] is False
    assert report["production_database_writes"] == 0
    assert "user:secret" not in str(report)


def test_unrecognized_hosts_and_unsafe_timeouts_are_rejected():
    with pytest.raises(ValueError):
        inspect_transport("example.org")
    with pytest.raises(ValueError):
        inspect_transport(CORE_SOURCE_HOSTS[0], timeout=100)


def test_error_categories():
    assert _error_category(TimeoutError()) == "connect_timeout"
    assert _error_category(ConnectionRefusedError()) == "connection_error"
