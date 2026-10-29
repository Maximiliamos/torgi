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
