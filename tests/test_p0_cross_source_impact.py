"""A shared cadastral number is not evidence that two auction sales are identical."""
import runpy
from pathlib import Path

ASSESS_PAIR = runpy.run_path(
    str(Path(__file__).resolve().parents[1] / "scripts/p0-cross-source-dedup-impact.py")
)["assess_pair"]


def test_identical_cadastral_property_is_not_confirmed_as_same_sale():
    result = ASSESS_PAIR(
        primary_cadastre="76:02:022201:38",
        duplicate_cadastre="76:02:022201:38",
        primary_region="76", duplicate_region="76",
        primary_status="active", duplicate_status="closed",
    )
    assert result == ("same_cadastre_not_sale_identity", "source_status_divergence")


def test_distinct_cadastre_and_region_have_separate_review_reasons():
    result = ASSESS_PAIR(
        primary_cadastre="76:02:022201:38",
        duplicate_cadastre="77:01:000101:5",
        primary_region="76", duplicate_region="77",
        primary_status="active", duplicate_status="active",
    )
    assert result == ("conflicting_cadastres", "region_conflict")


def test_readonly_impact_query_compiles_on_empty_sqlite_database():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from bankrotai.db import Base

    engine = create_engine("sqlite+pysqlite:///:memory:")
    try:
        Base.metadata.create_all(engine)
        with Session(engine) as session:
            report = runpy.run_path(
                str(Path(__file__).resolve().parents[1] /
                    "scripts/p0-cross-source-dedup-impact.py")
            )["build_report"](session)
            assert report["read_only"] is True
            assert report["no_mutations"] is True
            assert report["counts"] == {}
    finally:
        engine.dispose()


def test_multi_source_canonical_link_is_reported_without_processed_duplicate():
    """Old merges may be stored only as shared CanonicalLot links."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from bankrotai.db import Base, CanonicalLot, SourceLot

    engine = create_engine("sqlite+pysqlite:///:memory:")
    try:
        Base.metadata.create_all(engine)
        with Session(engine) as session:
            canonical = CanonicalLot(
                canonical_key="cadastral:76:02:022201:38", title="Земельный участок"
            )
            session.add(canonical)
            session.flush()
            session.add_all([
                SourceLot(
                    canonical_lot_id=canonical.id, source_system="m-ets.ru",
                    external_id="240000-1",
                ),
                SourceLot(
                    canonical_lot_id=canonical.id, source_system="cdtrf.ru",
                    external_id="cdtrf:390899",
                ),
            ])
            session.commit()
            report = runpy.run_path(
                str(Path(__file__).resolve().parents[1] /
                    "scripts/p0-cross-source-dedup-impact.py")
            )["build_report"](session)
            assert report["read_only"] is True
            assert report["counts"]["canonical_groups_multiple_sources"] == 1
            assert report["counts"].get("cross_source_merged_pairs", 0) == 0
            assert report["sample_ids"]["canonical_groups_multiple_sources"][0] == {
                "canonical_id": canonical.id,
            }
    finally:
        engine.dispose()



def test_new_verified_etp_sales_with_same_cadastre_persist_as_separate_canonicals():
    """Integration regression for P0: physical asset is not auction sale identity."""
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import Session
    from bankrotai.db import Base, ProcessedLot, SourceLot
    from bankrotai.domain import NormalizedLot
    from bankrotai.logic import persist_lot

    def auction(source: str, sale_id: str) -> NormalizedLot:
        return NormalizedLot(
            external_id=sale_id, source=source, source_system=source,
            title="Земельный участок, кадастр 76:02:022201:38",
            description="Продажа земельного участка",
            category="land", region_slug="yaroslavl",
            region_name="Ярославская область",
            address="Ярославская область, Ярославский район",
            cadastral_number="76:02:022201:38",
            vin=None, area=600,
            start_price=650000.0, current_price=650000.0,
            auction_status="active",
            lot_url=f"https://{source}/lot/{sale_id}",
            source_url=f"https://{source}/lot/{sale_id}",
            detail_level="public_detail", raw_data={"region_code": "76"},
        )

    engine = create_engine("sqlite+pysqlite:///:memory:")
    try:
        Base.metadata.create_all(engine)
        with Session(engine) as session:
            with session.begin():
                persist_lot(session, auction("m-ets.ru", "243003-1"), isolate_source=True)
                persist_lot(session, auction("cdtrf.ru", "cdtrf:390899"), isolate_source=True)
            processed = session.scalars(select(ProcessedLot)).all()
            sources = session.scalars(select(SourceLot)).all()
            assert len(processed) == 2
            assert len(sources) == 2
            assert all(p.duplicate_of_id is None for p in processed)
            assert len({s.canonical_lot_id for s in sources}) == 2
            first_id = next(
                s.canonical_lot_id for s in sources if s.source_system == "m-ets.ru"
            )
            session.commit()
            with session.begin():
                persist_lot(session, auction("m-ets.ru", "243003-1"), isolate_source=True)
            assert len(session.scalars(select(ProcessedLot)).all()) == 2
            assert next(
                s.canonical_lot_id for s in session.scalars(select(SourceLot)).all()
                if s.source_system == "m-ets.ru"
            ) == first_id
    finally:
        engine.dispose()
