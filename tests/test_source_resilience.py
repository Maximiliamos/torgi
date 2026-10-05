from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from bankrotai.db import Base, SourceHealthState, utc_now
from bankrotai.services.source_resilience import (
    SOURCE_CIRCUIT_COOLDOWN_SECONDS,
    classify_source_error,
    record_source_outcome,
    record_source_probe,
    source_circuit_blocks,
    source_resilience_status,
    source_retry_decision,
    sources_due_for_probe,
)


def _sessions():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


@pytest.mark.parametrize(
    ("message", "category", "operational", "retryable"),
    [
        ("socket.gaierror: Name or service not known", "dns", True, True),
        ("ConnectTimeout: connection timed out", "connect_timeout", True, True),
        ("ReadTimeout: read timed out", "read_timeout", True, True),
        ("SSLError: TLS handshake failed", "tls", True, True),
        ("HTTP 429 too many requests", "http_429", True, True),
        ("HTTP 503 upstream unavailable", "http_5xx", True, True),
        ("HTTP 403 forbidden", "authentication", False, False),
        ("access_limited by upstream", "access_limited", False, False),
        ("source coverage guard rejected reconciliation", "coverage_guard", False, True),
        ("JSONDecodeError: unexpected payload", "parser_contract", False, True),
        ("validation failed for payload", "validation", False, True),
        ("TypeError: connector contract bug", "internal_error", False, False),
    ],
)
def test_source_failure_classification(message, category, operational, retryable):
    value = classify_source_error(message)

    assert value.category == category
    assert value.operational is operational
    assert value.retryable is retryable


def test_operational_failures_open_only_the_source_circuit():
    factory = _sessions()
    now = utc_now().replace(tzinfo=None)
    with factory() as session:
        first = record_source_outcome(
            session,
            "bidexpert.ru",
            success=False,
            error="HTTP 503 upstream unavailable",
        )
        second = record_source_outcome(
            session,
            "bidexpert.ru",
            success=False,
            error="TLS handshake failed",
        )
        third = record_source_outcome(
            session,
            "bidexpert.ru",
            success=False,
            error="connection timed out",
        )
        session.commit()

        assert first["circuit_state"] == "closed"
        assert second["circuit_state"] == "closed"
        assert third["circuit_state"] == "open"
        assert third["consecutive_operational_failures"] == 3
        assert source_circuit_blocks(session, "bidexpert.ru") is True
        assert source_circuit_blocks(session, "torgi.gov.ru") is False

        after_cooldown = now + timedelta(seconds=SOURCE_CIRCUIT_COOLDOWN_SECONDS + 5)
        assert source_resilience_status(
            session,
            "bidexpert.ru",
            now=after_cooldown,
        )["circuit_state"] == "half_open"
        assert source_circuit_blocks(session, "bidexpert.ru", now=after_cooldown) is True
        assert sources_due_for_probe(session, now=after_cooldown) == ["bidexpert.ru"]


def test_semantic_failure_never_opens_network_circuit():
    factory = _sessions()
    with factory() as session:
        for _ in range(5):
            state = record_source_outcome(
                session,
                "torgi-russia.ru",
                success=False,
                error="source coverage guard rejected reconciliation",
            )
        session.commit()

    assert state["circuit_state"] == "closed"
    assert state["operational_failure"] is False
    assert state["consecutive_operational_failures"] == 0
    assert state["retryable"] is True


def test_success_closes_circuit_and_resets_operational_counter():
    factory = _sessions()
    with factory() as session:
        for _ in range(3):
            record_source_outcome(
                session,
                "lot-online.ru",
                success=False,
                error="HTTP 503 connection timeout",
            )
        recovered = record_source_outcome(
            session,
            "lot-online.ru",
            success=True,
            items_seen=123,
        )
        session.commit()

        row = session.scalar(
            select(SourceHealthState).where(SourceHealthState.source_system == "lot-online.ru")
        )

    assert recovered["circuit_state"] == "closed"
    assert recovered["consecutive_operational_failures"] == 0
    assert recovered["next_retry_at"] is None
    assert row is not None and row.items_seen == 123
    assert row.status == "healthy"


def test_probe_recovery_closes_circuit_without_claiming_data_freshness():
    factory = _sessions()
    with factory() as session:
        for _ in range(3):
            record_source_outcome(
                session,
                "bidexpert.ru",
                success=False,
                error="HTTP 503 upstream unavailable",
            )
        before = session.scalar(
            select(SourceHealthState).where(SourceHealthState.source_system == "bidexpert.ru")
        )
        assert before is not None and before.last_success_at is None

        status = record_source_probe(
            session,
            "bidexpert.ru",
            success=True,
        )
        session.commit()
        after = session.scalar(
            select(SourceHealthState).where(SourceHealthState.source_system == "bidexpert.ru")
        )

    assert status["circuit_state"] == "closed"
    assert status["last_probe_success_at"] is not None
    assert after is not None and after.last_success_at is None


def test_retry_decision_separates_network_and_operator_action_errors():
    factory = _sessions()
    with factory() as session:
        timeout = source_retry_decision(
            session,
            "bidexpert.ru",
            error="HTTP 503 connection timeout",
        )
        auth = source_retry_decision(
            session,
            "bidexpert.ru",
            error="HTTP 403 forbidden",
        )

    assert timeout["schedule"] is True
    assert timeout["countdown_seconds"] == 60
    assert timeout["last_error_category"] == "read_timeout"
    assert auth["schedule"] is False
    assert auth["reason"] == "non_retryable"
    assert auth["last_error_category"] == "authentication"
