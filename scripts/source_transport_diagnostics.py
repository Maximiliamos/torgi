"""Read-only DNS/TLS checks for five core TORGI production source hosts.

Does not call source APIs, log IPs, print proxy credentials, disable TLS
verification, alter DNS, or write production data. Checks direct TCP/TLS only;
results do not by themselves prove connector/parser or proxied HTTP health.

Usage: python scripts/source_transport_diagnostics.py --output transport.json
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
import socket
import ssl
from pathlib import Path

CORE_SOURCE_HOSTS = (
    "torgi.gov.ru",
    "lot-online.ru",
    "bidexpert.ru",
    "torgi-russia.ru",
    "tbankrot.ru",
)
PROXY_ENV = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy")


def _error_category(exc: BaseException) -> str:
    if isinstance(exc, socket.gaierror):
        return "dns_resolution"
    if isinstance(exc, ssl.SSLCertVerificationError):
        return "tls_certificate"
    if isinstance(exc, ssl.SSLError):
        return "tls_handshake"
    if isinstance(exc, TimeoutError):
        return "connect_timeout"
    if isinstance(exc, OSError):
        return "connection_error"
    return "unexpected_error"


def inspect_transport(
    host: str,
    *,
    timeout: float = 8.0,
    resolver=None,
    connector=None,
    context_factory=None,
) -> dict:
    if not 1 <= timeout <= 30:
        raise ValueError("timeout must be between 1 and 30 seconds")
    if host not in CORE_SOURCE_HOSTS:
        raise ValueError("only configured core source hosts may be checked")
    resolver = resolver or socket.getaddrinfo
    connector = connector or socket.create_connection
    context_factory = context_factory or ssl.create_default_context
    result = {"source": host, "dns": "not_checked", "tls": "not_checked"}
    try:
        addresses = resolver(host, 443, type=socket.SOCK_STREAM)
        if not addresses:
            raise socket.gaierror("no addresses")
        # Do not disclose resolved IP addresses in diagnostics.
        result["dns"] = "ok"
    except Exception as exc:
        result["dns"] = "failed"
        result["error_category"] = _error_category(exc)
        return result
    try:
        with connector((host, 443), timeout=timeout) as connection:
            with context_factory().wrap_socket(connection, server_hostname=host) as secured:
                result["tls"] = "ok"
                result["tls_version"] = secured.version()
    except Exception as exc:
        result["tls"] = "failed"
        result["error_category"] = _error_category(exc)
    return result


def survey(*, timeout: float = 8.0, inspector=None) -> dict:
    inspector = inspector or inspect_transport
    checked = [inspector(host, timeout=timeout) for host in CORE_SOURCE_HOSTS]
    return {
        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        "mode": "direct_dns_and_verified_tls_only",
        "sources": checked,
        "dns_successful": sum(item["dns"] == "ok" for item in checked),
        "tls_successful": sum(item["tls"] == "ok" for item in checked),
        "proxy_environment_present": any(os.environ.get(name) for name in PROXY_ENV),
        "api_http_checked": False,
        "production_database_writes": 0,
        "tls_verification_disabled": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout", type=float, default=8.0)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = survey(timeout=args.timeout)
    rendered = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
