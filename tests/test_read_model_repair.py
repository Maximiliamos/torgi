from contextlib import contextmanager

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from bankrotai.db import Base, CanonicalLot, ProcessedLot, SourceLot
from bankrotai.services.read_model_repair import audit_read_model_links, repair_missing_processed_links


def test_cli_audit_does_not_initialize_or_commit_database(monkeypatch, capsys) -> None:
    from bankrotai import cli
    from bankrotai.services import read_model_repair

    @contextmanager
    def read_only_session():
        yield object()

    monkeypatch.setattr(cli, "init_db", lambda: (_ for _ in ()).throw(AssertionError("migration attempted")))
    monkeypatch.setattr(cli, "session_scope", lambda: (_ for _ in ()).throw(AssertionError("write scope used")))
    monkeypatch.setattr(cli, "read_session_scope", read_only_session)
    monkeypatch.setattr(read_model_repair, "audit_read_model_links", lambda _session, *, limit: {"dry_run": True, "limit": limit})
    monkeypatch.setattr("sys.argv", ["bankrotai", "audit-read-model-links", "--limit", "7"])

    cli.main()

    assert '"limit": 7' in capsys.readouterr().out


def test_repair_missing_processed_link_is_idempotent() -> None:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        canonical = CanonicalLot(canonical_key="cadastral:76:23:1:1", title="Участок", category="land")
        session.add(canonical)
        session.flush()
        session.add(SourceLot(
            canonical_lot_id=canonical.id,
            source_system="torgi.gov.ru",
            external_id="gis-1",
            title="Участок",
            description="Ярославль",
            category="land",
            region_code="76",
            region_name="Ярославская область",
            address="Ярославль, ул. Свободы, 1",
            cadastral_number="76:23:1:1",
            start_price=100000,
            source_status="active",
            is_active=True,
            is_archived=False,
            raw_data={"region_code": "76"},
        ))
        session.commit()

        assert repair_missing_processed_links(session) == {"selected": 1, "repaired": 1}
        source = session.scalar(select(SourceLot))
        assert source is not None and source.processed_lot_id is not None
        assert session.scalar(select(func.count()).select_from(ProcessedLot)) == 1


def test_audit_only_marks_conflict_free_identity_match_repairable() -> None:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        canonical = CanonicalLot(canonical_key="audit:1", title="Asset", category="land")
        session.add(canonical)
        session.flush()
        processed = ProcessedLot(
            source_system="tbankrot.ru", source="tbankrot", external_id="one",
            title="Asset", description="", category="land", auction_status="active",
        )
        session.add(processed)
        session.flush()
        session.add_all([
            SourceLot(
                canonical_lot_id=canonical.id, source_system="tbankrot.ru", external_id="one",
                title="Asset", is_active=True, is_archived=False,
            ),
            SourceLot(
                canonical_lot_id=canonical.id, source_system="tbankrot.ru", external_id="missing",
                title="Missing", is_active=True, is_archived=False,
            ),
        ])
        session.commit()

        report = audit_read_model_links(session)

        assert report["dry_run"] is True
        assert report["counts_by_category"] == {
            "recoverable_legacy_link": 1,
            "active_missing_read_model": 1,
        }
        assert session.scalar(select(func.count()).select_from(ProcessedLot)) == 1
        assert all(row.processed_lot_id is None for row in session.scalars(select(SourceLot)))
        assert repair_missing_processed_links(session) == {"selected": 0, "repaired": 0}
        assert session.scalar(select(func.count()).select_from(ProcessedLot)) == 1
