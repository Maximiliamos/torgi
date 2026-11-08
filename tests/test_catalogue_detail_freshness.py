from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from bankrotai.db import Base, CanonicalLot, ProcessedLot, SourceLot
from bankrotai.services.catalogue_detail_freshness import (
    catalogue_candidate_fingerprint,
    select_catalogue_candidates,
    touch_unchanged_catalogue_rows,
)


def _sessions():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def _persist_verified_source(
    sessions,
    *,
    source_system: str,
    external_id: str,
    price: Decimal,
    source_status: str,
    raw_data: dict,
    last_seen_at: datetime,
    application_start_at: datetime | None = None,
    application_deadline: datetime | None = None,
):
    with sessions.begin() as session:
        processed = ProcessedLot(
            external_id=external_id,
            source=source_system,
            source_system=source_system,
            title="Квартира",
            description="Жилая недвижимость",
            category="apartment",
            auction_status=source_status,
        )
        session.add(processed)
        session.flush()
        canonical = CanonicalLot(
            canonical_key=f"{source_system}:{external_id}",
            legacy_processed_lot_id=processed.id,
            title="Квартира",
            category="apartment",
        )
        session.add(canonical)
        session.flush()
        session.add(
            SourceLot(
                canonical_lot_id=canonical.id,
                processed_lot_id=processed.id,
                source_system=source_system,
                external_id=external_id,
                title="Квартира",
                category="apartment",
                cadastral_number="76:23:010101:1",
                start_price=price,
                current_price=price,
                source_status=source_status,
                raw_data=raw_data,
                application_start_at=application_start_at,
                application_deadline=application_deadline,
                last_seen_at=last_seen_at,
            )
        )


def _mets_candidate(price: str = "1000000") -> dict:
    return {
        "external_id": "242937-2",
        "catalogue_price": price,
        "catalogue_status": "Торги в стадии приема заявок",
        "title": "Квартира",
    }


def _cdtrf_candidate(price: float = 1_000_000) -> dict:
    return {
        "external_id": "cdtrf:101",
        "trade_id": "77",
        "trade_lot_id": "101",
        "property_category_ids": [7],
        "region_id": 76,
        "source_status_text": "Прием заявок",
        "listing_price_rub": price,
        "request_time_begin": "2026-11-09T10:00:00",
        "request_time_end": "2026-12-27T18:00:00",
    }


def test_mets_legacy_verified_unchanged_candidate_skips_detail() -> None:
    sessions = _sessions()
    now = datetime(2026, 11, 8, 20, 0)
    _persist_verified_source(
        sessions,
        source_system="m-ets.ru",
        external_id="242937-2",
        price=Decimal("1000000"),
        source_status="active",
        raw_data={
            "catalogue_price_verified": True,
            "catalogue_status_verified": True,
        },
        last_seen_at=now - timedelta(hours=1),
    )
    with sessions() as session:
        decision = select_catalogue_candidates(
            session, "m-ets.ru", [_mets_candidate()], now=now,
        )["242937-2"]
    assert decision["skip_detail"] is True
    assert len(decision["fingerprint"]) == 64


def test_mets_changed_price_forces_detail() -> None:
    sessions = _sessions()
    now = datetime(2026, 11, 8, 20, 0)
    _persist_verified_source(
        sessions,
        source_system="m-ets.ru",
        external_id="242937-2",
        price=Decimal("1000000"),
        source_status="active",
        raw_data={
            "catalogue_price_verified": True,
            "catalogue_status_verified": True,
        },
        last_seen_at=now - timedelta(hours=1),
    )
    with sessions() as session:
        decision = select_catalogue_candidates(
            session, "m-ets.ru", [_mets_candidate("999000")], now=now,
        )["242937-2"]
    assert decision["skip_detail"] is False


def test_mets_stale_verified_detail_forces_refresh() -> None:
    sessions = _sessions()
    now = datetime(2026, 11, 8, 20, 0)
    _persist_verified_source(
        sessions,
        source_system="m-ets.ru",
        external_id="242937-2",
        price=Decimal("1000000"),
        source_status="active",
        raw_data={
            "catalogue_price_verified": True,
            "catalogue_status_verified": True,
        },
        last_seen_at=now - timedelta(days=8),
    )
    with sessions() as session:
        decision = select_catalogue_candidates(
            session, "m-ets.ru", [_mets_candidate()], now=now,
        )["242937-2"]
    assert decision["skip_detail"] is False


def test_cdtrf_legacy_verified_unchanged_candidate_skips_detail() -> None:
    sessions = _sessions()
    now = datetime(2026, 11, 8, 20, 0)
    candidate = _cdtrf_candidate()
    _persist_verified_source(
        sessions,
        source_system="cdtrf.ru",
        external_id="cdtrf:101",
        price=Decimal("1000000"),
        source_status="active",
        raw_data={
            "evidence": "url_html_nuxt_identity_match",
            "category_evidence": "lotInfo_and_official_badge",
            "trade_id": "77",
            "trade_lot_id": "101",
            "source_status_text": "Прием заявок",
        },
        last_seen_at=now - timedelta(hours=1),
        application_start_at=datetime(2026, 11, 9, 7, 0),
        application_deadline=datetime(2026, 12, 27, 15, 0),
    )
    with sessions() as session:
        decision = select_catalogue_candidates(
            session, "cdtrf.ru", [candidate], now=now,
        )["cdtrf:101"]
    assert decision["skip_detail"] is True


def test_unknown_candidate_never_skips_detail() -> None:
    sessions = _sessions()
    with sessions() as session:
        decision = select_catalogue_candidates(
            session, "cdtrf.ru", [_cdtrf_candidate()],
            now=datetime(2026, 11, 8, 20, 0),
        )["cdtrf:101"]
    assert decision["skip_detail"] is False


def test_explicit_catalogue_fingerprint_is_reused_and_liveness_advances() -> None:
    sessions = _sessions()
    now = datetime(2026, 11, 8, 20, 0)
    candidate = _mets_candidate()
    fingerprint = catalogue_candidate_fingerprint("m-ets.ru", candidate)
    _persist_verified_source(
        sessions,
        source_system="m-ets.ru",
        external_id="242937-2",
        price=Decimal("1000000"),
        source_status="active",
        raw_data={
            "catalogue_fingerprint": fingerprint,
            "catalogue_fingerprint_version": 1,
            "detail_verified_at": (now - timedelta(hours=1)).isoformat(),
        },
        last_seen_at=now - timedelta(hours=1),
    )
    with sessions.begin() as session:
        touched = touch_unchanged_catalogue_rows(
            session,
            "m-ets.ru",
            [{"external_id": "242937-2", "fingerprint": fingerprint}],
            run_id="incremental-test-run",
            observed_at=now,
        )
    assert touched == 1
    with sessions() as session:
        row = session.query(SourceLot).filter_by(
            source_system="m-ets.ru", external_id="242937-2"
        ).one()
        assert row.last_seen_at == now
        assert row.last_sync_run_id == "incremental-test-run"


def test_cdtrf_changed_application_deadline_forces_detail() -> None:
    sessions = _sessions()
    now = datetime(2026, 11, 8, 20, 0)
    candidate = _cdtrf_candidate()
    _persist_verified_source(
        sessions,
        source_system="cdtrf.ru",
        external_id="cdtrf:101",
        price=Decimal("1000000"),
        source_status="active",
        raw_data={
            "evidence": "url_html_nuxt_identity_match",
            "category_evidence": "lotInfo_and_official_badge",
            "trade_id": "77",
            "trade_lot_id": "101",
            "source_status_text": "Прием заявок",
        },
        last_seen_at=now - timedelta(hours=1),
        application_start_at=datetime(2026, 11, 9, 7, 0),
        application_deadline=datetime(2026, 12, 27, 15, 0),
    )
    candidate["request_time_end"] = "2026-12-28T18:00:00"
    with sessions() as session:
        decision = select_catalogue_candidates(
            session, "cdtrf.ru", [candidate], now=now,
        )["cdtrf:101"]
    assert decision["skip_detail"] is False
