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
