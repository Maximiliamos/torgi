from __future__ import annotations

import os
import re
import socket
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from sqlalchemy import select
from sqlalchemy.orm import Session

from bankrotai.db import SourceHealthState, utc_now


SOURCE_CIRCUIT_FAILURE_THRESHOLD = 3
SOURCE_CIRCUIT_COOLDOWN_SECONDS = 15 * 60
_SOURCE_RETRY_STEPS_SECONDS = (60, 180, 300, 900)

_SOURCE_PROBE_URLS = {
    "torgi.gov.ru": "https://torgi.gov.ru/",
    "lot-online.ru": "https://lot-online.ru/",
    "torgi-russia.ru": "https://torgi-russia.ru/",
    "bidexpert.ru": "https://bidexpert.ru/",
}


@dataclass(frozen=True, slots=True)
class SourceFailureClassification:
    category: str
    operational: bool
    retryable: bool
    reason: str
    http_status: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _error_text(error: BaseException | str | None) -> str:
    if error is None:
        return ""
    if isinstance(error, BaseException):
        values: list[str] = []
        current: BaseException | None = error
        seen: set[int] = set()
        while current is not None and id(current) not in seen:
            seen.add(id(current))
            values.append(f"{current.__class__.__name__}: {current}")
            current = current.__cause__ or current.__context__
        return " | ".join(values)
    return str(error)


def _http_status(message: str) -> int | None:
    match = re.search(r"(?<!\\d)([45]\\d{2})(?!\\d)", message)
    return int(match.group(1)) if match else None


def classify_source_error(error: BaseException | str | None) -> SourceFailureClassification:
    raw = _error_text(error)
    message = raw.casefold()
    status = _http_status(message)

    if any(
        marker in message
        for marker in (
            "uniqueviolation",
            "integrityerror",
            "duplicate key value violates unique constraint",
            "psycopg.errors",
        )
    ):
        return SourceFailureClassification("database_integrity", False, False, raw[:1000], status)
    if "coverage guard" in message:
        return SourceFailureClassification("coverage_guard", False, True, raw[:1000], status)
    if "access_limited" in message or "access limited" in message:
        return SourceFailureClassification("access_limited", False, False, raw[:1000], status)
    if status == 429 or "rate limit" in message or "too many requests" in message:
        return SourceFailureClassification("http_429", True, True, raw[:1000], 429)
    if status in {401, 403} or any(
        marker in message for marker in ("unauthorized", "forbidden", "authentication required")
    ):
        return SourceFailureClassification("authentication", False, False, raw[:1000], status)
    if any(
        marker in message
        for marker in (
            "name or service not known",
            "temporary failure in name resolution",
            "nodename nor servname",
            "getaddrinfo",
            "dns resolution",
            "dns error",
        )
    ):
        return SourceFailureClassification("dns", True, True, raw[:1000], status)
    if any(
        marker in message
        for marker in (
            "ssl",
            "tls",
            "certificate verify",
            "schannel",
            "handshake",
        )
    ):
        return SourceFailureClassification("tls", True, True, raw[:1000], status)
    if any(marker in message for marker in ("connect timeout", "connection timed out", "failed to connect")):
        return SourceFailureClassification("connect_timeout", True, True, raw[:1000], status)
    if any(marker in message for marker in ("read timeout", "read timed out")):
        return SourceFailureClassification("read_timeout", True, True, raw[:1000], status)
    if "timeout" in message or "timed out" in message:
        return SourceFailureClassification("read_timeout", True, True, raw[:1000], status)
    if status is not None and 500 <= status <= 599:
        return SourceFailureClassification("http_5xx", True, True, raw[:1000], status)
    if any(
        marker in message
        for marker in (
            "connection reset",
            "connection refused",
            "connection aborted",
            "server disconnected",
            "network is unreachable",
            "remote end closed",
        )
    ):
        return SourceFailureClassification("unknown_upstream", True, True, raw[:1000], status)
    if any(
        marker in message
        for marker in (
            "jsondecodeerror",
            "parser",
            "parse error",
            "unexpected html",
            "selector",
            "contract violation",
            "schema changed",
        )
    ):
        return SourceFailureClassification("parser_contract", False, True, raw[:1000], status)
    if any(marker in message for marker in ("validation", "invalid payload", "invalid response")):
        return SourceFailureClassification("validation", False, True, raw[:1000], status)
    if any(
        marker in message
        for marker in (
            "traceback (most recent call last)",
            "attributeerror",
            "typeerror",
            "keyerror",
            "assertionerror",
        )
    ):
        return SourceFailureClassification("internal_error", False, False, raw[:1000], status)
    return SourceFailureClassification("unknown_upstream", True, True, raw[:1000], status)


def source_network_fingerprint() -> dict[str, Any]:
    """Return non-secret runtime network facts useful for grouping incidents."""
    return {
        "hostname": socket.gethostname(),
        "http_proxy_configured": bool(os.environ.get("HTTP_PROXY") or os.environ.get("http_proxy")),
        "https_proxy_configured": bool(os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")),
        "no_proxy_configured": bool(os.environ.get("NO_PROXY") or os.environ.get("no_proxy")),
    }


def _parse_timestamp(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def _state(session: Session, source_system: str) -> SourceHealthState:
    state = session.scalar(
        select(SourceHealthState).where(SourceHealthState.source_system == source_system)
    )
    if state is None:
        state = SourceHealthState(source_system=source_system, status="unknown", metadata_json={})
        session.add(state)
        session.flush()
    return state


def source_resilience_status(
    session: Session,
    source_system: str,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = (now or utc_now()).replace(tzinfo=None)
    state = session.scalar(
        select(SourceHealthState).where(SourceHealthState.source_system == source_system)
    )
    metadata = dict(state.metadata_json or {}) if state is not None else {}
    circuit_state = str(metadata.get("circuit_state") or "closed")
    open_until = _parse_timestamp(metadata.get("circuit_open_until"))
    if circuit_state == "open" and open_until is not None and open_until <= current:
        circuit_state = "half_open"
    return {
        "source_system": source_system,
        "circuit_state": circuit_state,
        "circuit_open_until": open_until,
        "next_retry_at": _parse_timestamp(metadata.get("next_retry_at")),
        "last_error_category": metadata.get("last_error_category"),
        "retryable": bool(metadata.get("retryable", False)),
        "operational_failure": bool(metadata.get("operational_failure", False)),
        "consecutive_operational_failures": int(metadata.get("consecutive_operational_failures") or 0),
        "last_operational_failure_at": _parse_timestamp(metadata.get("last_operational_failure_at")),
        "last_probe_at": _parse_timestamp(metadata.get("last_probe_at")),
        "last_probe_success_at": _parse_timestamp(metadata.get("last_probe_success_at")),
        "network_fingerprint": metadata.get("network_fingerprint") or {},
    }


def source_circuit_blocks(
    session: Session,
    source_system: str,
    *,
    now: datetime | None = None,
) -> bool:
    return source_resilience_status(session, source_system, now=now)["circuit_state"] in {"open", "half_open"}


def _retry_delay(classification: SourceFailureClassification, consecutive_failures: int) -> int | None:
    if not classification.retryable:
        return None
    if classification.category in {"coverage_guard", "parser_contract", "validation"}:
        return 30 * 60
    if classification.category == "http_429":
        return 5 * 60
    index = min(max(consecutive_failures, 1) - 1, len(_SOURCE_RETRY_STEPS_SECONDS) - 1)
    return _SOURCE_RETRY_STEPS_SECONDS[index]


def record_source_outcome(
    session: Session,
    source_system: str,
    *,
    success: bool,
    error: BaseException | str | None = None,
    items_seen: int | None = None,
) -> dict[str, Any]:
    now = utc_now().replace(tzinfo=None)
    state = _state(session, source_system)
    metadata = dict(state.metadata_json or {})

    if success:
        metadata.update(
            {
                "circuit_state": "closed",
                "circuit_open_until": None,
                "next_retry_at": None,
                "last_error_category": None,
                "retryable": False,
                "operational_failure": False,
                "consecutive_operational_failures": 0,
                "network_fingerprint": source_network_fingerprint(),
            }
        )
        state.status = "healthy"
        state.last_success_at = now
        state.last_error = None
        if items_seen is not None:
            state.items_seen = max(0, int(items_seen))
        state.metadata_json = metadata
        state.updated_at = now
        session.flush()
        return source_resilience_status(session, source_system, now=now)

    classification = classify_source_error(error)
    consecutive = int(metadata.get("consecutive_operational_failures") or 0)
    if classification.operational:
        consecutive += 1
        metadata["last_operational_failure_at"] = now.isoformat()
    else:
        consecutive = 0
    delay = _retry_delay(classification, consecutive)
    circuit_open = (
        classification.operational
        and consecutive >= SOURCE_CIRCUIT_FAILURE_THRESHOLD
    )
    open_until = (
        now + timedelta(seconds=SOURCE_CIRCUIT_COOLDOWN_SECONDS)
        if circuit_open
        else None
    )
    next_retry = open_until or (now + timedelta(seconds=delay) if delay is not None else None)
    metadata.update(
        {
            "circuit_state": "open" if circuit_open else "closed",
            "circuit_open_until": open_until.isoformat() if open_until else None,
            "next_retry_at": next_retry.isoformat() if next_retry else None,
            "last_error_category": classification.category,
            "retryable": classification.retryable,
            "operational_failure": classification.operational,
            "consecutive_operational_failures": consecutive,
            "network_fingerprint": source_network_fingerprint(),
        }
    )
    state.status = "failed"
    state.last_failure_at = now
    state.last_error = (classification.reason or classification.category)[:5000]
    state.metadata_json = metadata
    state.updated_at = now
    session.flush()
    return source_resilience_status(session, source_system, now=now)


def source_retry_decision(
    session: Session,
    source_system: str,
    *,
    error: BaseException | str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = (now or utc_now()).replace(tzinfo=None)
    status = source_resilience_status(session, source_system, now=current)
    if status["circuit_state"] == "open":
        open_until = status["circuit_open_until"]
        seconds = max(1, int((open_until - current).total_seconds())) if open_until else SOURCE_CIRCUIT_COOLDOWN_SECONDS
        return {
            "schedule": False,
            "reason": "circuit_open",
            "countdown_seconds": seconds,
            **status,
        }

    classification = classify_source_error(error) if error is not None else None
    if classification is not None and not status.get("last_error_category"):
        status["last_error_category"] = classification.category
        status["retryable"] = classification.retryable
        status["operational_failure"] = classification.operational
    retryable = (
        classification.retryable
        if classification is not None
        else bool(status.get("retryable"))
    )
    if not retryable:
        return {
            "schedule": False,
            "reason": "non_retryable",
            "countdown_seconds": None,
            **status,
        }

    next_retry = status.get("next_retry_at")
    if next_retry is not None:
        delay = max(1, int((next_retry - current).total_seconds()))
    else:
        delay = _retry_delay(
            classification or SourceFailureClassification(
                str(status.get("last_error_category") or "unknown_upstream"),
                bool(status.get("operational_failure")),
                True,
                "",
            ),
            int(status.get("consecutive_operational_failures") or 1),
        ) or 60
    return {
        "schedule": True,
        "reason": "retryable",
        "countdown_seconds": delay,
        **status,
    }


def sources_due_for_probe(session: Session, *, now: datetime | None = None) -> list[str]:
    current = (now or utc_now()).replace(tzinfo=None)
    values: list[str] = []
    for state in session.scalars(select(SourceHealthState)).all():
        status = source_resilience_status(session, state.source_system, now=current)
        if status["circuit_state"] == "half_open":
            values.append(state.source_system)
    return sorted(values)


def probe_source_endpoint(source_system: str, *, timeout_seconds: float = 5.0) -> dict[str, Any]:
    url = _SOURCE_PROBE_URLS.get(source_system)
    if not url:
        return {"success": False, "error": "probe_not_configured", "source_system": source_system}
    try:
        request = Request(url, method="HEAD", headers={"User-Agent": "BankrotAI-source-health/1.0"})
        with urlopen(request, timeout=timeout_seconds) as response:
            status = int(getattr(response, "status", 200))
        if status == 429 or status >= 500:
            raise RuntimeError(f"HTTP {status} from source probe")
        return {"success": True, "source_system": source_system, "http_status": status, "url": url}
    except HTTPError as exc:
        status = int(exc.code)
        if status != 429 and status < 500:
            return {"success": True, "source_system": source_system, "http_status": status, "url": url}
        classification = classify_source_error(f"HTTP {status}: {exc}")
    except Exception as exc:
        classification = classify_source_error(exc)
    return {
        "success": False,
        "source_system": source_system,
        "url": url,
        "classification": classification.to_dict(),
        "error": classification.reason,
    }


def record_source_probe(
    session: Session,
    source_system: str,
    *,
    success: bool,
    error: BaseException | str | None = None,
) -> dict[str, Any]:
    now = utc_now().replace(tzinfo=None)
    state = _state(session, source_system)
    metadata = dict(state.metadata_json or {})
    metadata["last_probe_at"] = now.isoformat()
    metadata["network_fingerprint"] = source_network_fingerprint()

    if success:
        metadata.update(
            {
                "circuit_state": "closed",
                "circuit_open_until": None,
                "next_retry_at": now.isoformat(),
                "consecutive_operational_failures": 0,
                "last_probe_success_at": now.isoformat(),
                "operational_failure": False,
            }
        )
    else:
        classification = classify_source_error(error)
        metadata.update(
            {
                "last_error_category": classification.category,
                "retryable": classification.retryable,
                "operational_failure": classification.operational,
            }
        )
        if classification.operational:
            open_until = now + timedelta(seconds=SOURCE_CIRCUIT_COOLDOWN_SECONDS)
            metadata["circuit_state"] = "open"
            metadata["circuit_open_until"] = open_until.isoformat()
            metadata["next_retry_at"] = open_until.isoformat()

    state.metadata_json = metadata
    state.updated_at = now
    session.flush()
    return source_resilience_status(session, source_system, now=now)
