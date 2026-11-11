from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from bankrotai.db import AppSetting, Base, GeoFailure, ProcessedLot
from bankrotai.geo import CadastralObjectResult
from bankrotai.services.geo_resilience import GeoProviderUnavailable
from bankrotai.services.managed_stale_cadastral_recovery import (
    managed_recovery_state,
    run_managed_recovery_batch,
)


def _factory():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False), engine


def _seed(
    session: Session,
    *,
    source: str = "torgi-russia.ru",
    cadastral: str = "76:13:031301:92",
    suffix: str = "",
) -> int:
    lot = ProcessedLot(
        external_id=f"managed-{source}-{cadastral}-{suffix}",
        source=source,
        source_system=source,
        title="Земельный участок",
        description="",
        category="land",
        cadastral_number=cadastral,
        auction_status="active",
    )
    session.add(lot)
    session.flush()
    session.add(
        GeoFailure(
            lot_id=lot.id,
            status="deferred_no_match",
            attempt_count=3,
            error_message=(
                '{"error":"No validated geocoding result","attempts":'
                '[{"source":"nspd_cadastral","reason":"no_coordinates"}],'
                '"classification":"no_match"}'
            ),
        )
    )
    session.commit()
    return int(lot.id)


def test_managed_recovery_cursor_advances_across_confirmed_no_matches() -> None:
    factory, engine = _factory()
    with factory() as session:
        ids = [
            _seed(session, cadastral=f"76:13:031301:{90 + index}", suffix=str(index))
            for index in range(3)
        ]

    calls: list[str] = []

    def no_match(cadastral: str):
        calls.append(cadastral)
        return None, "live_nspd_no_match"

    first = run_managed_recovery_batch(
        factory,
        live_result_probe=no_match,
        batch_size=2,
        apply=True,
        version="cursor-test",
        sources=("torgi-russia.ru",),
    )
    assert first["status"] == "running"
    assert first["selected_ids"] == ids[:2]
    assert first["cursor_after"] == ids[1]
    assert first["updated"] == 0

    second = run_managed_recovery_batch(
        factory,
        live_result_probe=no_match,
        batch_size=2,
        apply=True,
        version="cursor-test",
        sources=("torgi-russia.ru",),
    )
    assert second["status"] == "completed"
    assert second["selected_ids"] == ids[2:]
    assert second["cursor_after"] == ids[2]
    assert len(calls) == 3

    with factory() as session:
        state = managed_recovery_state(
            session,
            version="cursor-test",
            sources=("torgi-russia.ru",),
        )
        assert state["completed_sources"] == ["torgi-russia.ru"]
        assert state["probed"] == 3
        assert state["updated"] == 0
    engine.dispose()


def test_managed_recovery_persists_same_live_result_without_second_probe() -> None:
    factory, engine = _factory()
    with factory() as session:
        lot_id = _seed(session)

    calls: list[str] = []

    def exact(cadastral: str):
        calls.append(cadastral)
        return (
            CadastralObjectResult(
                query=cadastral,
                cadastral_number=cadastral,
                address="Ярославская область, Ростовский район",
                lat=57.1,
                lon=39.4,
                source="nspd",
                confidence="high",
            ),
            "exact_match",
        )

    result = run_managed_recovery_batch(
        factory,
        live_result_probe=exact,
        batch_size=100,
        apply=True,
        version="write-test",
        sources=("torgi-russia.ru",),
    )

    assert calls == ["76:13:031301:92"]
    assert result["status"] == "completed"
    assert result["accepted"] == 1
    assert result["updated"] == 1
    with factory() as session:
        lot = session.get(ProcessedLot, lot_id)
        failure = session.query(GeoFailure).filter_by(lot_id=lot_id).one()
        assert lot.current_geo_lat == 57.1
        assert lot.current_geo_lon == 39.4
        assert lot.current_geo_source == "nspd"
        assert failure.status == "resolved"
    engine.dispose()


def test_managed_recovery_operational_failure_holds_cursor_for_retry() -> None:
    factory, engine = _factory()
    with factory() as session:
        ids = [
            _seed(session, cadastral=f"76:13:031301:{120 + index}", suffix=str(index))
            for index in range(3)
        ]

    failed_cadastral = "76:13:031301:121"

    def flaky(cadastral: str):
        if cadastral == failed_cadastral:
            raise GeoProviderUnavailable("nspd", "read_timeout")
        return None, "live_nspd_no_match"

    first = run_managed_recovery_batch(
        factory,
        live_result_probe=flaky,
        batch_size=3,
        apply=True,
        version="retry-test",
        sources=("torgi-russia.ru",),
    )
    assert first["status"] == "retry_required"
    assert first["operational_failures"] == 1
    assert first["cursor_after"] == ids[0]

    second_calls: list[str] = []

    def recovered(cadastral: str):
        second_calls.append(cadastral)
        return None, "live_nspd_no_match"

    second = run_managed_recovery_batch(
        factory,
        live_result_probe=recovered,
        batch_size=3,
        apply=True,
        version="retry-test",
        sources=("torgi-russia.ru",),
    )
    assert second["selected_ids"] == ids[1:]
    assert second["status"] == "completed"
    assert second_calls == ["76:13:031301:121", "76:13:031301:122"]
    engine.dispose()


def test_managed_recovery_skips_empty_source_and_persists_source_order() -> None:
    factory, engine = _factory()
    with factory() as session:
        lot_id = _seed(session, source="torgi.gov.ru", cadastral="77:01:0004010:123")

    result = run_managed_recovery_batch(
        factory,
        live_result_probe=lambda _cadastral: (None, "live_nspd_no_match"),
        batch_size=10,
        apply=True,
        version="source-rollover-test",
        sources=("torgi-russia.ru", "torgi.gov.ru"),
    )
    assert result["source"] == "torgi.gov.ru"
    assert result["selected_ids"] == [lot_id]
    assert result["status"] == "completed"

    with factory() as session:
        setting = session.query(AppSetting).filter(
            AppSetting.key.like("geo_managed_stale_recovery:%")
        ).one()
        assert "source-rollover-test" in setting.key
    engine.dispose()
