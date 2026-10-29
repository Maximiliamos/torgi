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
