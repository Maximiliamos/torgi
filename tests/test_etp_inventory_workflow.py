"""No background ETP inventory may run after a failed or stale Home deploy."""
from pathlib import Path

YAML = (Path(__file__).resolve().parents[1] /
        ".github/workflows/etp-22-source-readonly-inventory.yml").read_text(
    encoding="utf-8"
)


def test_home_deploy_trigger_and_weekly_readonly_inventory():
    assert 'workflows: ["Deploy home secondary origin"]' in YAML
    assert "github.event.workflow_run.conclusion == 'success'" in YAML
    assert "github.event.workflow_run.head_branch == 'main'" in YAML
    assert 'cron: "11 3 * * 1"' in YAML
    assert "cancel-in-progress: true" in YAML


def test_version_proof_precedes_catalogue_probe():
    assert YAML.index('bankrotai-home-api:$expected') < YAML.index(
        'python -m bankrotai.services.etp_source_inventory'
    )
    assert "Home worker SHA changed before inventory" in YAML
    assert "--limit 5 --delay-seconds 1.0" in YAML
    assert "docker exec bankrotai-home-ingestion-worker" in YAML


def test_survey_report_is_preserved_without_raw_html_or_database_writes():
    assert "registered_profile_count -ne 22" in YAML
    assert "probed_source_count -ne 22" in YAML
    assert "production_database_writes -ne 0" in YAML
    assert "map_publication -ne $false" in YAML
    assert "Retain sanitized 22-source evidence for readiness review" in YAML
    assert "actions/upload-artifact@" in YAML
    assert "retention-days: 30" in YAML
