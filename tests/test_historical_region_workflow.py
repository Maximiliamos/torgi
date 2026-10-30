"""Historical region audit can only run on a trusted Home SHA, without writes."""
from pathlib import Path

WF = (Path(__file__).resolve().parents[1] /
      ".github/workflows/p13-historical-region-review.yml").read_text(
          encoding="utf-8"
      )


def test_region_proof_runs_only_on_exact_successful_home():
    assert 'workflows: ["Deploy home secondary origin"]' in WF
    assert "github.event.workflow_run.conclusion == 'success'" in WF
    assert "github.event.workflow_run.head_branch == 'main'" in WF
    assert "github.ref == 'refs/heads/main'" in WF
    assert "github.event.workflow_run.head_sha || github.sha" in WF
    assert 'bankrotai-home-api:$expected' in WF
    assert WF.index('bankrotai-home-api:$expected') < WF.index(
        "python -m bankrotai.services.historical_region_review --limit 50"
    )


def test_region_proof_cannot_publish_or_claim_backfill():
    assert "sample_exhaustive -ne $false" in WF
    assert "same_as_full_p13_eligibility_population -ne $false" in WF
    assert "historical_region_backfill_completed -ne $false" in WF
    assert "restore_verified_backup_required_before_write -ne $true" in WF
    assert "no_mutations -ne $true" in WF
    assert "sample_size -gt 50" in WF
    assert "p13-historical-region-review.json" in WF
    assert "retention-days: 30" in WF
