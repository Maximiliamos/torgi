from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
import socket
import ssl
import time
from typing import Any

import requests
from redis import Redis

from bankrotai.core import get_settings

_OPERATIONAL_CATEGORIES = {
    "dns_error",
    "connect_timeout",
    "read_timeout",
    "connection_error",
    "tls_error",
    "rate_limited",
    "provider_5xx",
    "provider_protocol",
    "circuit_open",
}
_PROVIDER_FAILURE_THRESHOLD = 3
_PROVIDER_CIRCUIT_SECONDS = 300
_GLOBAL_FAILURE_WINDOW_SECONDS = 120
_GLOBAL_CIRCUIT_SECONDS = 90
_STATE_TTL_SECONDS = 86_400
_PROVIDER_PREFIX = "bankrotai:geo:provider-health:"
_GLOBAL_KEY = "bankrotai:geo:external-network"
_SNAPSHOT_KEY = "bankrotai:geo:network-snapshot"

EXTERNAL_PROVIDERS = frozenset({"nspd", "ik12", "nominatim", "probe:nspd", "probe:ik12", "probe:torgi"})
LOCAL_PROVIDERS = frozenset({"photon"})
_EXTERNAL_DEPENDENCIES: dict[str, tuple[str, ...]] = {
    "nspd": ("nspd", "probe:nspd"),
    "ik12": ("ik12", "probe:ik12"),
    "nominatim": ("nominatim",),
    "torgi": ("probe:torgi",),
}
_PROBE_TO_PROVIDER = {
    "probe:nspd": "nspd",
    "probe:ik12": "ik12",
}


class GeoProviderUnavailable(RuntimeError):
    def __init__(self, provider: str, category: str, message: str = "") -> None:
        super().__init__(message or f"{provider} unavailable: {category}")
        self.provider = provider
        self.category = category


@dataclass(slots=True)
class ProviderHealth:
    provider: str
    consecutive_failures: int = 0
    last_category: str | None = None
    last_failure_at: float | None = None
    last_success_at: float | None = None
    circuit_open_until: float | None = None
    latency_ms: float | None = None


def classify_transport_exception(exc: BaseException) -> str:
    text = f"{exc.__class__.__name__}: {exc}".casefold()
    if isinstance(exc, requests.exceptions.HTTPError):
        status = exc.response.status_code if exc.response is not None else None
        if status == 429:
            return "rate_limited"
        if status is not None and status >= 500:
            return "provider_5xx"
        return "provider_protocol"
    if isinstance(exc, requests.exceptions.SSLError) or "ssl" in text or "tls" in text:
        return "tls_error"
    if isinstance(exc, requests.exceptions.ConnectTimeout):
        return "connect_timeout"
    if isinstance(exc, requests.exceptions.ReadTimeout):
        return "read_timeout"
    if isinstance(exc, requests.exceptions.Timeout) or "timed out" in text or "timeout" in text:
        return "read_timeout"
    if isinstance(exc, requests.exceptions.ConnectionError):
        if any(marker in text for marker in ("name resolution", "nameresolution", "failed to resolve", "getaddrinfo", "no address associated", "nodename")):
            return "dns_error"
        return "connection_error"
    if isinstance(exc, socket.gaierror):
        return "dns_error"
    if isinstance(exc, TimeoutError):
        return "connect_timeout"
    return "provider_protocol"


def is_operational_category(category: str | None) -> bool:
    return str(category or "") in _OPERATIONAL_CATEGORIES


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


def _load_json(key: str) -> dict[str, Any]:
    client = _redis()
    if client is None:
        return {}
    try:
        raw = client.get(key)
        return json.loads(raw) if raw else {}
    except Exception:
        return {}
    finally:
        try:
            client.close()
        except Exception:
            pass


def _save_json(key: str, payload: dict[str, Any], ttl: int = _STATE_TTL_SECONDS) -> None:
    client = _redis()
    if client is None:
        return
    try:
        client.set(key, json.dumps(payload, separators=(",", ":"), ensure_ascii=False), ex=ttl)
    except Exception:
        pass
    finally:
        try:
            client.close()
        except Exception:
            pass


def _provider_key(provider: str) -> str:
    return f"{_PROVIDER_PREFIX}{provider}"


def provider_health(provider: str) -> ProviderHealth:
    payload = _load_json(_provider_key(provider))
    return ProviderHealth(
        provider=provider,
        consecutive_failures=int(payload.get("consecutive_failures") or 0),
        last_category=payload.get("last_category"),
        last_failure_at=float(payload["last_failure_at"]) if payload.get("last_failure_at") else None,
        last_success_at=float(payload["last_success_at"]) if payload.get("last_success_at") else None,
        circuit_open_until=float(payload["circuit_open_until"]) if payload.get("circuit_open_until") else None,
        latency_ms=float(payload["latency_ms"]) if payload.get("latency_ms") is not None else None,
    )


def record_provider_success(provider: str, *, latency_ms: float | None = None) -> ProviderHealth:
    now = time.time()
    state = ProviderHealth(
        provider=provider,
        consecutive_failures=0,
        last_category=None,
        last_success_at=now,
        circuit_open_until=None,
        latency_ms=round(latency_ms, 1) if latency_ms is not None else None,
    )
    _save_json(_provider_key(provider), asdict(state))
    _refresh_global_circuit(now)
    return state


def record_provider_failure(
    provider: str,
    category: str,
    *,
    latency_ms: float | None = None,
) -> ProviderHealth:
    now = time.time()
    previous = provider_health(provider)
    failures = previous.consecutive_failures + 1
    open_until = previous.circuit_open_until
    if failures >= _PROVIDER_FAILURE_THRESHOLD:
        open_until = max(float(open_until or 0), now + _PROVIDER_CIRCUIT_SECONDS)
    state = ProviderHealth(
        provider=provider,
        consecutive_failures=failures,
        last_category=category,
        last_failure_at=now,
        last_success_at=previous.last_success_at,
        circuit_open_until=open_until,
        latency_ms=round(latency_ms, 1) if latency_ms is not None else previous.latency_ms,
    )
    _save_json(_provider_key(provider), asdict(state))
    _refresh_global_circuit(now)
    return state


def _dependency_health(dependency: str) -> tuple[float | None, float | None, str | None]:
    providers = _EXTERNAL_DEPENDENCIES.get(dependency, ())
    states = [provider_health(provider) for provider in providers]
    last_failure_at = max((state.last_failure_at or 0.0) for state in states) or None
    last_success_at = max((state.last_success_at or 0.0) for state in states) or None
    latest_failure = max(
        (state for state in states if state.last_failure_at),
        key=lambda state: float(state.last_failure_at or 0.0),
        default=None,
    )
    return (
        last_failure_at,
        last_success_at,
        latest_failure.last_category if latest_failure is not None else None,
    )


def _refresh_global_circuit(now: float | None = None) -> dict[str, Any]:
    now = now or time.time()
    degraded: list[str] = []
    healthy_recent = 0
    for dependency in sorted(_EXTERNAL_DEPENDENCIES):
        last_failure_at, last_success_at, last_category = _dependency_health(dependency)
        if (
            last_failure_at
            and now - last_failure_at <= _GLOBAL_FAILURE_WINDOW_SECONDS
            and is_operational_category(last_category)
            and (not last_success_at or last_success_at < last_failure_at)
        ):
            degraded.append(dependency)
        elif (
            last_success_at
            and now - last_success_at <= _GLOBAL_FAILURE_WINDOW_SECONDS
            and (not last_failure_at or last_success_at >= last_failure_at)
        ):
            healthy_recent += 1
    current = _load_json(_GLOBAL_KEY)
    open_until = float(current.get("circuit_open_until") or 0)
    if len(degraded) >= 2:
        open_until = max(open_until, now + _GLOBAL_CIRCUIT_SECONDS)
    elif healthy_recent >= 2:
        open_until = 0
    elif open_until <= now:
        open_until = 0
    payload = {
        "degraded_providers": degraded,
        "circuit_open_until": open_until or None,
        "updated_at": now,
    }
    _save_json(_GLOBAL_KEY, payload, ttl=600)
    return payload


def provider_allowed(provider: str, *, external: bool) -> bool:
    now = time.time()
    state = provider_health(provider)
    if state.circuit_open_until and state.circuit_open_until > now:
        return False
    if external:
        global_state = _refresh_global_circuit(now)
        if float(global_state.get("circuit_open_until") or 0) > now:
            return False
    return True


def provider_recovered_since(provider: str, failed_at: datetime | float | None) -> bool:
    if failed_at is None:
        return False
    failed_epoch = (
        failed_at.timestamp()
        if isinstance(failed_at, datetime)
        else float(failed_at)
    )
    external = provider in EXTERNAL_PROVIDERS and provider not in LOCAL_PROVIDERS
    if not provider_allowed(provider, external=external):
        return False
    state = provider_health(provider)
    return bool(state.last_success_at and state.last_success_at > failed_epoch)


def require_provider(provider: str, *, external: bool) -> None:
    if not provider_allowed(provider, external=external):
        raise GeoProviderUnavailable(provider, "circuit_open")


def global_network_state() -> dict[str, Any]:
    state = _refresh_global_circuit()
    now = time.time()
    state["circuit_open"] = float(state.get("circuit_open_until") or 0) > now
    return state


def _probe_tls(host: str, *, timeout: float = 2.0) -> tuple[str, float]:
    started = time.monotonic()
    try:
        addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise GeoProviderUnavailable(host, "dns_error", str(exc)) from exc
    if not addresses:
        raise GeoProviderUnavailable(host, "dns_error", "no DNS addresses")
    raw = None
    try:
        raw = socket.create_connection((host, 443), timeout=timeout)
        context = ssl.create_default_context()
        with context.wrap_socket(raw, server_hostname=host):
            pass
        raw = None
    except ssl.SSLError as exc:
        raise GeoProviderUnavailable(host, "tls_error", str(exc)) from exc
    except TimeoutError as exc:
        raise GeoProviderUnavailable(host, "connect_timeout", str(exc)) from exc
    except OSError as exc:
        raise GeoProviderUnavailable(host, "connection_error", str(exc)) from exc
    finally:
        if raw is not None:
            try:
                raw.close()
            except Exception:
                pass
    return str(addresses[0][4][0]), (time.monotonic() - started) * 1000


def _network_fingerprint(resolved: dict[str, str | None]) -> str:
    parts = [f"{key}={resolved.get(key) or ''}" for key in sorted(resolved)]
    try:
        with open("/etc/resolv.conf", "r", encoding="utf-8", errors="ignore") as handle:
            parts.append(handle.read(4096))
    except OSError:
        pass
    try:
        with open("/proc/net/route", "r", encoding="utf-8", errors="ignore") as handle:
            parts.append(handle.read(4096))
    except OSError:
        pass
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()[:16]


def probe_geo_network_health() -> dict[str, Any]:
    targets = {
        "probe:nspd": "nspd.gov.ru",
        "probe:ik12": "api.roscadastres.com",
        "probe:torgi": "torgi.gov.ru",
    }
    probes: dict[str, dict[str, Any]] = {}
    resolved: dict[str, str | None] = {}
    for provider, host in targets.items():
        started = time.monotonic()
        try:
            address, latency_ms = _probe_tls(host)
            resolved[provider] = address
            record_provider_success(provider, latency_ms=latency_ms)
            if mirrored := _PROBE_TO_PROVIDER.get(provider):
                record_provider_success(mirrored, latency_ms=latency_ms)
            probes[provider] = {
                "ok": True,
                "latency_ms": round(latency_ms, 1),
                "category": None,
            }
        except GeoProviderUnavailable as exc:
            latency_ms = (time.monotonic() - started) * 1000
            resolved[provider] = None
            record_provider_failure(provider, exc.category, latency_ms=latency_ms)
            if mirrored := _PROBE_TO_PROVIDER.get(provider):
                record_provider_failure(mirrored, exc.category, latency_ms=latency_ms)
            probes[provider] = {
                "ok": False,
                "latency_ms": round(latency_ms, 1),
                "category": exc.category,
            }

    photon_url = os.getenv("PHOTON_BASE_URL", "")
    started = time.monotonic()
    try:
        if not photon_url:
            raise GeoProviderUnavailable("photon", "provider_protocol", "PHOTON_BASE_URL is empty")
        response = requests.get(
            f"{photon_url.rstrip('/')}/api",
            params={"q": "Ярославль", "limit": 1, "lang": "ru", "countrycode": "RU"},
            timeout=(1, 2),
        )
        response.raise_for_status()
        latency_ms = (time.monotonic() - started) * 1000
        record_provider_success("photon", latency_ms=latency_ms)
        probes["photon"] = {"ok": True, "latency_ms": round(latency_ms, 1), "category": None}
    except Exception as exc:
        latency_ms = (time.monotonic() - started) * 1000
        category = exc.category if isinstance(exc, GeoProviderUnavailable) else classify_transport_exception(exc)
        record_provider_failure("photon", category, latency_ms=latency_ms)
        probes["photon"] = {"ok": False, "latency_ms": round(latency_ms, 1), "category": category}

    previous = _load_json(_SNAPSHOT_KEY)
    fingerprint = _network_fingerprint(resolved)
    previous_fingerprint = previous.get("network_fingerprint") if isinstance(previous, dict) else None
    snapshot = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "network_fingerprint": fingerprint,
        "previous_network_fingerprint": previous_fingerprint,
        "network_changed": bool(previous_fingerprint and previous_fingerprint != fingerprint),
        "probes": probes,
        "external": global_network_state(),
    }
    _save_json(_SNAPSHOT_KEY, snapshot, ttl=600)
    return snapshot


def network_health_snapshot() -> dict[str, Any]:
    snapshot = _load_json(_SNAPSHOT_KEY)
    providers = {
        provider: asdict(provider_health(provider))
        for provider in sorted(EXTERNAL_PROVIDERS | LOCAL_PROVIDERS)
    }
    return {
        "snapshot": snapshot or None,
        "providers": providers,
        "external": global_network_state(),
    }


def retry_delay_seconds(category: str, attempt: int) -> int | None:
    attempt = max(1, int(attempt))
    if is_operational_category(category):
        operational_schedule: tuple[int, ...] = (60, 180, 300, 900)
        return operational_schedule[min(attempt - 1, len(operational_schedule) - 1)]
    if category == "no_match":
        no_match_schedule: tuple[int, ...] = (1800, 7200)
        return no_match_schedule[attempt - 1] if attempt <= len(no_match_schedule) else None
    if category == "validation":
        validation_schedule: tuple[int, ...] = (300, 1800)
        return validation_schedule[attempt - 1] if attempt <= len(validation_schedule) else None
    internal_schedule: tuple[int, ...] = (300, 1800, 7200)
    return internal_schedule[attempt - 1] if attempt <= len(internal_schedule) else None
