"""Ensure official ETP bounded cross-page evidence runs on exact Home SHA only."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / ".github" / "workflows"


def test_fivepage_validator_is_protected_and_readonly_on_both_sources():
    for short, source in (("mets", "m-ets.ru"), ("cdtrf", "cdtrf.ru")):
        file = (ROOT / f"etp-{short}-pagination-readonly.yml").read_text(
            encoding="utf-8"
        )
        assert 'workflows: ["Deploy home secondary origin"]' in file
        assert "github.event.workflow_run.conclusion == 'success'" in file
        assert "github.event.workflow_run.head_branch == 'main'" in file
        assert 'bankrotai-home-api:$expected' in file
        sha_check = file.index('bankrotai-home-api:$expected')
        runner = file.index(
            f"python -m bankrotai.services.etp_fivepage_readonly --source {source}"
        )
        assert sha_check < runner
        assert "$report.pages_verified -ne 5" in file
        assert "$report.read_only -ne $true" in file
        assert "$report.no_database_writes -ne $true" in file
        assert "$report.safe_for_archival -ne $false" in file
        assert "$report.complete_source_inventory -ne $false" in file
        assert "$report.reconcile_missing -ne $false" in file
        assert "$report.map_publication_verified -ne $false" in file
        assert "catalogue_candidates_seen -ne $report.unique_catalogue_id_fingerprints" in file
        assert f"{short}-fivepage-crosspage-readonly.json" in file
        assert f"name: {short}-fivepage-crosspage-readonly" in file
        assert "if-no-files-found: warn" in file
        assert "retention-days: 30" in file
