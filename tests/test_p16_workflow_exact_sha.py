"""P13/P16 map data-impact evidence must reference current deployed code and stay read-only."""
from pathlib import Path

WF = (Path(__file__).resolve().parents[1] /
      ".github/workflows/p16-read-only-baseline.yml").read_text(encoding="utf-8")


def test_auto_baseline_only_after_successful_main_home_deploy():
    assert 'workflows: ["Deploy home secondary origin"]' in WF
    assert "github.event.workflow_run.conclusion == 'success'" in WF
    assert "github.event.workflow_run.head_branch == 'main'" in WF
    assert "ref: ${{ github.event.workflow_run.head_sha || github.sha }}" in WF


def test_impact_report_is_readonly_and_exact_home_code():
    check = WF.index("Verify read-only impact report uses exact deployed Home image")
    audit = WF.index("Audit existing Home production database without writes")
    assert check < audit
    assert 'bankrotai-home-api:$expected' in WF
    assert "p13-production-eligibility-impact.py" in WF
    assert "scripts/p16-readonly-baseline.py" in WF
    assert "read_only" in WF and "no_mutations" in WF
