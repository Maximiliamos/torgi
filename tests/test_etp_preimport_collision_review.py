"""S5 pre-import collision checks are read-only and conservative."""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session

from bankrotai.db import Base, CanonicalLot, ProcessedLot, SourceLot
from bankrotai.services import etp_manual_import
from bankrotai.services.etp_preimport_collision_review import review_canary_collisions


def candidate(external_id, cadastre="76:02:022201:38", source="m-ets.ru"):
    return SimpleNamespace(
        external_id=external_id, source_system=source,
        cadastral_number=cadastre, category="land",
        auction_status="active", current_price=500000,
        start_price=None, lot_url=f"https://m-ets.ru/{external_id}",
    )


def saved_lot(session, external_id, source, cadastre, archived=False):
    lot = ProcessedLot(
        external_id=external_id, source=source, source_system=source,
        title="Property", description="", category="land",
        cadastral_number=cadastre, is_archived=archived, auction_status="active",
    )
    session.add(lot)
    session.flush()
    return lot


def test_preview_counts_cross_source_conflicts_without_any_dml():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as session:
            saved_lot(session, "old", "lot-online.ru", "76:02:022201:38")
            session.commit()
        mutations = []
        def guard(conn, cursor, statement, parameters, context, many):
            if statement.lstrip().upper().startswith(
                ("INSERT", "UPDATE", "DELETE", "ALTER", "CREATE", "DROP")
            ):
                mutations.append(statement)
                raise AssertionError("Read-only collision review attempted a database write")
        event.listen(engine, "before_cursor_execute", guard)
        with Session(engine) as session:
            report = review_canary_collisions(
                session, [candidate("243003-1"),
                          candidate("243003-2", "77:01:000101:2")],
                source="m-ets.ru"
            )
            assert report["details_reviewed"] == 2
            assert report["new_unconflicted_candidate_count"] == 1
            assert report["blocked_candidate_count"] == 1
            assert report["reason_counts"] == {
                "cross_source_cadastre_overlap": 1,
                "no_known_identity_collision": 1,
            }
            assert report["ready_for_separate_operator_canary"] is False
            assert report["race_free_persistence_guaranteed"] is False
            assert report["database_writes"] == 0
            assert "243003" not in str(report)
            assert "76:02" not in str(report)
            session.rollback()
            assert mutations == []
    finally:
        engine.dispose()


def test_existing_source_and_processed_identity_does_not_count_as_new():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as session:
            existing = saved_lot(
                session, "243003-1", "m-ets.ru", "76:02:022201:38"
            )
            canonical = CanonicalLot(
                canonical_key="review-previous-source",
                title="Previous",
            )
            session.add(canonical)
            session.flush()
            session.add(SourceLot(
                canonical_lot_id=canonical.id, processed_lot_id=existing.id,
                source_system="m-ets.ru", external_id="243003-1",
            ))
            session.commit()
            report = review_canary_collisions(
                session, [candidate("243003-1")], source="m-ets.ru"
            )
            assert report["reason_counts"]["already_processed_source_id"] == 1
            assert report["ready_for_separate_operator_canary"] is False
            assert session.scalar(select(ProcessedLot.id)) == existing.id
    finally:
        engine.dispose()


def test_duplicate_cadastral_in_two_new_details_is_blocked():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as session:
            report = review_canary_collisions(
                session, [candidate("243003-1"), candidate("243003-2")],
                source="m-ets.ru"
            )
            assert report["new_unconflicted_candidate_count"] == 1
            assert report["reason_counts"]["duplicate_cadastre_within_canary"] == 1
            assert not report["ready_for_separate_operator_canary"]
    finally:
        engine.dispose()


@pytest.mark.parametrize("source,details", [
    ("rts-tender.ru", [candidate("243003-1")]),
    ("m-ets.ru", []),
    ("m-ets.ru", [candidate("1"), candidate("2"), candidate("3")]),
    ("m-ets.ru", [candidate("same"), candidate("same")]),
    ("m-ets.ru", [candidate("wrong-source", source="cdtrf.ru")]),
])
def test_unapproved_source_or_unverified_candidate_fails_closed(source, details):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as session:
            with pytest.raises(ValueError):
                review_canary_collisions(session, details, source=source)
    finally:
        engine.dispose()


def test_collision_mode_cannot_be_combined_with_apply_even_if_operator_gates_set(
    monkeypatch,
):
    monkeypatch.setenv("ETP_OPERATOR_CANARY_IMPORT_ENABLED", "1")
    monkeypatch.setenv("ETP_CANARY_BACKUP_VERIFIED", "1")
    with pytest.raises(ValueError, match="cannot be combined"):
        etp_manual_import.run_import(
            "m-ets.ru", limit=2, apply=True, audit_collisions=True
        )


def test_import_preview_calls_only_sql_reads_and_returns_public_safe_counts(monkeypatch):
    from bankrotai import db

    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    try:
        async def verified(source, limit):
            assert limit == 1
            return [candidate("243003-1")]
        monkeypatch.setattr(etp_manual_import, "collect_lots", verified)
        monkeypatch.setattr(db, "SessionLocal", lambda: Session(engine))
        report = etp_manual_import.run_import(
            "m-ets.ru", limit=1, apply=False, audit_collisions=True
        )
        assert report["persisted_count"] == 0
        assert report["write_authorized"] is False
        audit = report["preimport_collision_review"]
        assert audit["new_unconflicted_candidate_count"] == 1
        assert audit["ready_for_separate_operator_canary"] is True
        with Session(engine) as session:
            assert session.scalar(select(ProcessedLot.id)) is None
    finally:
        engine.dispose()
