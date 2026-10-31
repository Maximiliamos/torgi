"""Regression checks for CDTRF import exception visibility and no-write triage."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CAMPAIGN = ROOT / ".github" / "workflows" / "cdtrf-one-time-import-geo-map.yml"
DIAGNOSTICS = ROOT / ".github" / "workflows" / "cdtrf-source-diagnostics.yml"


def test_import_stderr_is_preserved_without_powershell_receive_job_abort() -> None:
    workflow = CAMPAIGN.read_text(encoding="utf-8")
    assert "First full CDTRF catalogue and validated detail import" in workflow
    assert "$logPath = Join-Path $env:RUNNER_TEMP 'cdtrf-import-python-output.log'" in workflow
    assert '2>&1' in workflow
    assert "Write-Output ([int]$LASTEXITCODE)" in workflow
    assert "$output = @(Receive-Job -Job $job -Wait)" not in workflow
    assert "cdtrf-import-failure.json" in workflow
    assert "Traceback" in workflow
    assert "if ($jobState -ne 'Completed' -or $exitCode -ne 0" in workflow
    assert "if ($report.status -ne 'success'" in workflow
    assert "$manual = '${{ github.event_name }}' -eq 'workflow_dispatch'" in workflow
    assert "-not (Test-Path -LiteralPath $marker)" in workflow
    assert "Manual CDTRF import refused: no successful exact-SHA P1 production audit" in workflow


def test_readonly_diagnostic_does_not_import_or_geocode() -> None:
    workflow = DIAGNOSTICS.read_text(encoding="utf-8")
    assert "workflow_dispatch:" in workflow
    assert "python -m bankrotai.services.etp_cdtrf_campaign progress" in workflow
    assert "python -m bankrotai.services.etp_cdtrf_campaign audit" in workflow
    assert "python -m bankrotai.services.etp_cdtrf_campaign import" not in workflow
    assert "python -m bankrotai.services.etp_cdtrf_campaign geocode" not in workflow
    assert "ETP_OPERATOR_CANARY_IMPORT_ENABLED" not in workflow
    assert "database_writes = 0" in workflow
    assert "cdtrf-last-import-diagnostics.json" in workflow
