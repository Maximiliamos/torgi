"""P16/P17 guard regressions (no real production data is modified)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from bankrotai.db import Base, ProcessedLot


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("p16_public_map_dry_run", ROOT / "scripts/p16-public-map-dry-run.py")
assert spec is not None and spec.loader is not None
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


def test_p16_dry_run_finds_historical_pollution_without_mutating() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all([
            ProcessedLot(
                external_id="76:22:010717:536", source="test", source_system="torgi.gov.ru",
                title="Аренда земельного участка 76:22:010717:536",
                description="", category="land", auction_status="active",
                current_geo_lat=57.0, current_geo_lon=39.0,
            ),
            ProcessedLot(
                external_id="76:02:022201:38", source="test", source_system="tbankrot.ru",
                title="Земельный участок 76:02:022201:38",
                description="", category="land", auction_status="closed",
                cadastral_number="76:02:022201:38",
                current_geo_source="photon", current_geo_lat=57.5, current_geo_lon=39.5,
            ),
        ])
        session.commit()
        report = audit.audit_public_map_candidates(session)
        assert report["dry_run"] is True
        assert report["mutation_count"] == 0
        assert report["counts"]["rental"] == 1
        assert report["counts"]["closed_or_unknown_status"] == 1
        assert report["counts"]["cadastral_address_fallback_unverified"] == 1
        assert report["counts"]["legacy_tbankrot_without_fresh_source"] == 1
        assert session.query(ProcessedLot).count() == 2


def test_manual_regru_deploy_waits_for_home_before_touching_host() -> None:
    workflow = (ROOT / ".github/workflows/regru-deploy.yml").read_text(encoding="utf-8")
    job = workflow[workflow.index("  build-and-deploy:"):]
    wait_start = job.index("      - name: Wait for exact home deployment before staging REG.RU")
    wait_end = job.index("      - name: Prepare SSH identity")
    wait = job[wait_start:wait_end]
    assert "if: github.event_name == 'push'" not in wait
    assert "head_sha=$GITHUB_SHA" in wait
    assert "Deploy home secondary origin" in wait
    assert wait_start < wait_end < job.index("      - name: Prepare server and release HTTPS port")
    assert "Exact home deployment failed" in wait


def test_public_map_preflight_rejects_contamination() -> None:
    from bankrotai.services.public_map_quality import public_map_preflight

    point = {
        "title": "Аренда здания", "category": "real_estate",
        "status": "closed", "is_archived": False, "vin": None,
        "source_system": "torgi.gov.ru",
        "cadastral_number": "76:22:010717:536",
        "geo_source": "photon",
    }
    report = public_map_preflight([point])
    assert report["ok"] is False
    assert report["public_rental_count"] == 1
    assert report["public_closed_count"] == 1
    assert report["cadastral_address_fallback_count"] == 1


def test_public_map_preflight_keeps_exact_active_sale() -> None:
    from bankrotai.services.public_map_quality import public_map_preflight

    point = {
        "title": "Продажа земельного участка", "category": "land",
        "status": "active", "is_archived": False,
        "source_system": "torgi.gov.ru", "independent_source_verified": True,
        "cadastral_number": "76:23:010101:1", "geo_source": "nspd",
        "geo_confidence": "high",
    }
    report = public_map_preflight([point])
    assert report["ok"] is True
    assert report["point_count"] == 1
    assert report["public_rental_count"] == report["public_closed_count"] == 0


def test_home_api_sha_is_exposed_and_checked_on_all_deploy_paths() -> None:
    home_workflow = (ROOT / ".github/workflows/home-secondary-deploy.yml").read_text(encoding="utf-8")
    api_code = (ROOT / "src/bankrotai/api.py").read_text(encoding="utf-8")
    regru_workflow = (ROOT / ".github/workflows/regru-deploy.yml").read_text(encoding="utf-8")
    assert '--env "BANKROTAI_DEPLOY_SHA=$env:GITHUB_SHA"' in home_workflow
    assert 'os.getenv("BANKROTAI_DEPLOY_SHA"' in api_code
    assert regru_workflow.count('get("deployment_sha","")') >= 2
    assert 'if [ "$LIVE_HOME_SHA" != "$GITHUB_SHA" ]; then' in regru_workflow


def test_p16_repair_is_dry_run_and_preserves_source_provenance() -> None:
    from bankrotai.db import CanonicalLot, SourceLot, LotStatusHistory

    module_spec = importlib.util.spec_from_file_location(
        "p16_public_map_repair", ROOT / "scripts/p16-public-map-repair.py"
    )
    assert module_spec is not None and module_spec.loader is not None
    module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(module)
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        lot = ProcessedLot(
            source="torgi", source_system="torgi.gov.ru", external_id="76:22:010717:536",
            title="Аренда земельного участка 76:22:010717:536", description="",
            category="land", auction_status="active",
        )
        session.add(lot)
        session.flush()
        canonical = CanonicalLot(
            canonical_key="p16-raw-history", legacy_processed_lot_id=lot.id,
            title=lot.title, category="land",
        )
        session.add(canonical)
        session.flush()
        source_lot = SourceLot(
            canonical_lot_id=canonical.id, processed_lot_id=lot.id,
            source_system="torgi.gov.ru", external_id="raw-same-id",
        )
        session.add(source_lot)
        session.commit()
        first = module.repair_candidate_batch(session, apply=False)
        assert first["proposed_rows_in_first_batch"] == 1
        assert first["archived_rows"] == 0
        assert session.get(ProcessedLot, lot.id).is_archived is False
        done = module.repair_candidate_batch(session, apply=True)
        assert done["archived_rows"] == 1
        assert session.get(ProcessedLot, lot.id).is_archived is True
        assert session.get(SourceLot, source_lot.id).archive_reason == "rental"
        assert session.query(LotStatusHistory).count() == 1
        assert session.query(ProcessedLot).count() == 1


def test_p16_bounded_geo_canary_preserves_old_point_as_evidence() -> None:
    from bankrotai.db import LotGeoSnapshot

    spec = importlib.util.spec_from_file_location(
        "p16_geo_repair", ROOT / "scripts/p16-public-map-repair.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        lot = ProcessedLot(
            source="test", source_system="test", external_id="76:02:022201:38",
            title="Земельный участок", description="", category="land",
            region_code="76", cadastral_number="76:02:022201:38",
            auction_status="active", current_geo_lat=57.55, current_geo_lon=39.82,
            current_geo_source="photon", current_geo_confidence="high",
        )
        session.add(lot)
        session.commit()
        audit = module.quarantine_weak_cadastral_geo(session, limit=25, apply=False)
        assert audit["candidate_count"] == 1
        assert session.get(ProcessedLot, lot.id).current_geo_lat == 57.55
        changed = module.quarantine_weak_cadastral_geo(session, limit=25, apply=True)
        assert changed["changed"] == 1
        assert session.get(ProcessedLot, lot.id).current_geo_lat is None
        assert session.get(ProcessedLot, lot.id).needs_geo_check is True
        snapshot = session.query(LotGeoSnapshot).one()
        assert snapshot.centroid_lat == 57.55
        assert snapshot.geo_method == "p16_quarantined_geo_hint"
