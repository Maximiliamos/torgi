from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
import socket
import ssl
import threading
import time
from typing import Any

import requests
from redis import Redis

from bankrotai.core import get_settings


_PROVIDER_PREFIX = "bankrotai:geo:provider-health:"
_NETWORK_KEY = "bankrotai:geo:network-health"
_STATE_TTL_SECONDS = 86_400
_PROVIDER_OPEN_AFTER = 2
_PROVIDER_OPEN_SECONDS = {
    "photon": 120,
    "nspd": 300,
    "ik12": 300,
    "nominatim": 300,
}
_NETWORK_RECOVERY_SUCCESSES = 2
_LOCK = threading.RLock()
_PROVIDER_STATE: dict[str, dict[str, Any]] = {}
_NETWORK_STATE: dict[str, Any] = {}


_OPERATIONAL_CATEGORIES = frozenset({
    "dns_error",
    "connect_timeout",
    "read_timeout",
    "connection_error",
    "tls_error",
    "rate_limited",
    "server_error",
    "provider_unavailable",
    "provider_circuit_open",
    "external_network_down",
    "local_service_unavailable",
})


@dataclass(slots=True)
class GeoProviderOperationalError(RuntimeError):
    provider: str
    category: str
    detail: str = ""

    def __post_init__(self) -> None:
        RuntimeError.__init__(self, f"{self.provider}:{self.category}:{self.detail}"[:500])


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _redis() -> Redis | None:
    try:
        return Redis.from_url(
            get_settings().redis_url,
            socket_connect_timeout=1,
            socket_timeout=1,
            decode_responses=True,
        )
    except Exception:
        return None


def _read_redis_json(key: str) -> dict[str, Any] | None:
    client = _redis()
    if client is None:
        return None
    try:
        raw = client.get(key)
        if not raw:
            return None
        value = json.loads(raw)
        return value if isinstance(value, dict) else None
    except Exception:
        return None
    finally:
        try:
            client.close()
        except Exception:
            pass


def _write_redis_json(key: str, value: dict[str, Any]) -> None:
    client = _redis()
    if client is None:
        return
    try:
        client.set(key, json.dumps(value, ensure_ascii=False, separators=(",", ":")), ex=_STATE_TTL_SECONDS)
    except Exception:
        pass
    finally:
        try:
            client.close()
        except Exception:
            pass


def classify_operational_exception(exc: BaseException) -> str:
    if isinstance(exc, socket.gaierror):
        return "dns_error"
    if isinstance(exc, requests.exceptions.ConnectTimeout):
        return "connect_timeout"
    if isinstance(exc, requests.exceptions.ReadTimeout):
        return "read_timeout"
    if isinstance(exc, requests.exceptions.Timeout):
        return "read_timeout"
    if isinstance(exc, requests.exceptions.SSLError):
        return "tls_error"
    if isinstance(exc, requests.exceptions.HTTPError):
        status = getattr(exc.response, "status_code", None)
        if status == 429:
            return "rate_limited"
        if isinstance(status, int) and status >= 500:
            return "server_error"
        return "connection_error"
    if isinstance(exc, requests.exceptions.ConnectionError):
        message = str(exc).casefold()
        if any(marker in message for marker in ("nameresolution", "name resolution", "getaddrinfo", "no address associated", "name or service not known", "dns")):
            return "dns_error"
        return "connection_error"

    message = str(exc).casefold()
    if any(marker in message for marker in ("nameresolution", "name resolution", "getaddrinfo", "no address associated", "name or service not known", "dns")):
        return "dns_error"
    if "timed out" in message or "timeout" in message:
        return "read_timeout"
    if "ssl" in message or "tls" in message or "certificate" in message:
        return "tls_error"
    if "429" in message:
        return "rate_limited"
    if any(code in message for code in ("500", "502", "503", "504")):
        return "server_error"
    return "connection_error"


def is_operational_reason(reason: str | None) -> bool:
    value = str(reason or "")
    if value.startswith("operational:"):
        return True
    return value in _OPERATIONAL_CATEGORIES


def operational_category_from_reason(reason: str | None) -> str | None:
    value = str(reason or "")
    if value.startswith("operational:"):
        value = value.split(":", 1)[1]
    return value if value in _OPERATIONAL_CATEGORIES else None


def _provider_state(provider: str) -> dict[str, Any]:
    with _LOCK:
        cached = _PROVIDER_STATE.get(provider)
        if cached is not None:
            return dict(cached)
    restored = _read_redis_json(f"{_PROVIDER_PREFIX}{provider}")
    state = restored or {
        "provider": provider,
        "state": "unknown",
        "consecutive_failures": 0,
        "consecutive_successes": 0,
        "opened_until_epoch": None,
        "last_error_category": None,
        "last_latency_ms": None,
        "updated_at": None,
    }
    with _LOCK:
        _PROVIDER_STATE[provider] = dict(state)
    return dict(state)


def _save_provider_state(provider: str, state: dict[str, Any]) -> dict[str, Any]:
    with _LOCK:
        _PROVIDER_STATE[provider] = dict(state)
    _write_redis_json(f"{_PROVIDER_PREFIX}{provider}", state)
    return dict(state)


def provider_available(provider: str) -> bool:
    state = _provider_state(provider)
    opened_until = state.get("opened_until_epoch")
    if state.get("state") != "open" or not isinstance(opened_until, (int, float)):
        return True
    if time.time() >= float(opened_until):
        state["state"] = "half_open"
        state["opened_until_epoch"] = None
        state["updated_at"] = _now_iso()
        _save_provider_state(provider, state)
        return True
    return False


def record_provider_success(provider: str, *, latency_ms: float | None = None) -> dict[str, Any]:
    state = _provider_state(provider)
    state["consecutive_failures"] = 0
    state["consecutive_successes"] = int(state.get("consecutive_successes") or 0) + 1
    state["last_error_category"] = None
    state["opened_until_epoch"] = None
    state["last_latency_ms"] = round(float(latency_ms), 1) if latency_ms is not None else state.get("last_latency_ms")
    state["state"] = "healthy"
    state["updated_at"] = _now_iso()
    return _save_provider_state(provider, state)


def record_provider_failure(
    provider: str,
    category: str,
    *,
    detail: str | None = None,
    latency_ms: float | None = None,
) -> dict[str, Any]:
    state = _provider_state(provider)
    failures = int(state.get("consecutive_failures") or 0) + 1
    state["consecutive_failures"] = failures
    state["consecutive_successes"] = 0
    state["last_error_category"] = category
    state["last_error"] = str(detail or "")[:300] or None
    state["last_latency_ms"] = round(float(latency_ms), 1) if latency_ms is not None else state.get("last_latency_ms")
    if failures >= _PROVIDER_OPEN_AFTER:
        state["state"] = "open"
        state["opened_until_epoch"] = time.time() + int(_PROVIDER_OPEN_SECONDS.get(provider, 300))
    else:
        state["state"] = "degraded"
        state["opened_until_epoch"] = None
    state["updated_at"] = _now_iso()
    return _save_provider_state(provider, state)


def _network_state() -> dict[str, Any]:
    with _LOCK:
        if _NETWORK_STATE:
            return dict(_NETWORK_STATE)
    restored = _read_redis_json(_NETWORK_KEY)
    state = restored or {
        "state": "unknown",
        "consecutive_successes": 0,
        "failed_external_probes": 0,
        "fingerprint": None,
        "fingerprint_changed": False,
        "updated_at": None,
    }
    with _LOCK:
        _NETWORK_STATE.update(state)
    return dict(state)


def _save_network_state(state: dict[str, Any]) -> dict[str, Any]:
    with _LOCK:
        _NETWORK_STATE.clear()
        _NETWORK_STATE.update(state)
    _write_redis_json(_NETWORK_KEY, state)
    return dict(state)


def external_network_available() -> bool:
    return _network_state().get("state") not in {"down"}


def runtime_network_fingerprint() -> str:
    parts: list[str] = []
    for path in ("/etc/resolv.conf", "/proc/net/route"):
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as handle:
                text = handle.read()
            if path.endswith("resolv.conf"):
                text = "\n".join(
                    line.strip() for line in text.splitlines()
                    if line.strip().startswith(("nameserver", "search", "options"))
                )
            else:
                rows = text.splitlines()
                header = rows[:1]
                defaults = [row for row in rows[1:] if "\t00000000\t" in row or " 00000000 " in row]
                text = "\n".join([*header, *defaults])
            parts.append(f"{path}:{text}")
        except OSError:
            continue
    parts.append(f"hostname:{socket.gethostname()}")
    return hashlib.sha256("\n".join(parts).encode("utf-8", errors="replace")).hexdigest()


def _probe_tcp_tls(host: str, port: int = 443, timeout: float = 3.0) -> tuple[bool, str | None, float]:
    started = time.monotonic()
    try:
        socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        with socket.create_connection((host, port), timeout=timeout) as raw:
            context = ssl.create_default_context()
            with context.wrap_socket(raw, server_hostname=host):
                pass
        return True, None, (time.monotonic() - started) * 1000
    except Exception as exc:
        return False, classify_operational_exception(exc), (time.monotonic() - started) * 1000


def _probe_photon(timeout: float = 3.0) -> tuple[bool, str | None, float]:
    base = (os.getenv("PHOTON_BASE_URL") or "").rstrip("/")
    if not base:
        return False, "local_service_unavailable", 0.0
    started = time.monotonic()
    try:
        response = requests.get(
            f"{base}/api",
            params={"q": "Ярославль", "limit": 1, "lang": "ru", "countrycode": "RU"},
            timeout=(1.5, timeout),
        )
        response.raise_for_status()
        return True, None, (time.monotonic() - started) * 1000
    except Exception as exc:
        return False, classify_operational_exception(exc), (time.monotonic() - started) * 1000


def probe_geo_network() -> dict[str, Any]:
    previous = _network_state()
    fingerprint = runtime_network_fingerprint()
    provider_results: dict[str, dict[str, Any]] = {}

    photon_ok, photon_category, photon_latency = _probe_photon()
    if photon_ok:
        record_provider_success("photon", latency_ms=photon_latency)
    else:
        record_provider_failure("photon", photon_category or "local_service_unavailable", latency_ms=photon_latency)
    provider_results["photon"] = {
        "ok": photon_ok,
        "category": photon_category,
        "latency_ms": round(photon_latency, 1),
        "scope": "local",
    }

    external_hosts = {
        "nspd": "nspd.gov.ru",
        "ik12": "api.roscadastres.com",
        "torgi_gov": "torgi.gov.ru",
    }
    failed_external = 0
    for provider, host in external_hosts.items():
        ok, category, latency = _probe_tcp_tls(host)
        if provider in {"nspd", "ik12"}:
            if ok:
                record_provider_success(provider, latency_ms=latency)
            else:
                record_provider_failure(provider, category or "connection_error", latency_ms=latency)
        provider_results[provider] = {
            "ok": ok,
            "category": category,
            "latency_ms": round(latency, 1),
            "scope": "external",
        }
        failed_external += 0 if ok else 1

    target = "down" if failed_external >= 2 else "degraded" if failed_external == 1 else "healthy"
    successes = int(previous.get("consecutive_successes") or 0)
    if target == "healthy":
        successes += 1
        if previous.get("state") in {"down", "recovering"} and successes < _NETWORK_RECOVERY_SUCCESSES:
            target = "recovering"
    else:
        successes = 0

    state = {
        "state": target,
        "consecutive_successes": successes,
        "failed_external_probes": failed_external,
        "fingerprint": fingerprint,
        "fingerprint_changed": bool(previous.get("fingerprint") and previous.get("fingerprint") != fingerprint),
        "provider_results": provider_results,
        "updated_at": _now_iso(),
    }
    _save_network_state(state)

    recovered_providers = [
        provider
        for provider, result in provider_results.items()
        if result["ok"] and _provider_state(provider).get("state") == "healthy"
    ]
    return {
        **state,
        "previous_state": previous.get("state"),
        "network_recovered": previous.get("state") in {"down", "recovering"} and target == "healthy",
        "recovered_providers": recovered_providers,
    }


def resilience_snapshot() -> dict[str, Any]:
    providers = {
        provider: _provider_state(provider)
        for provider in ("photon", "nspd", "ik12", "nominatim")
    }
    network = _network_state()
    return {
        "network": network,
        "providers": providers,
    }
