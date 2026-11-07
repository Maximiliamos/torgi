from pathlib import Path


WORKFLOW = Path(".github/workflows/phase3-lite-full-reconcile.yml")


def test_phase3_accepts_only_evidenced_quarantine_partial() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "function Test-QuarantineOnlyPartial" in text
    assert "$quarantined -gt 0" in text
    assert "[int]$Row.items_archived -eq 0" in text
    assert "[int]$Row.items_failed -eq 0" in text
    assert "$phase -eq 'complete_with_quarantine'" in text
    assert "quarantined malformed record\\(s\\)" in text


def test_phase3_torgi_russia_is_non_reconciling_and_quarantine_partial_skips_retry() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "$nonReconcilingFullSources = @('torgi-russia.ru','m-ets.ru','cdtrf.ru','nistp.ru')" in text
    assert "-not (Test-QuarantineOnlyPartial $_) -and (" in text
    assert "$retryState.status -in @('success','partial')" in text
    assert "Targeted retry for $source completed with quarantine-only partial" in text
    assert "Targeted retry for $source did not finish as success or quarantine-only partial" in text


def test_phase3_summary_exposes_quarantine_evidence() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "| Archived | Quarantined | Rejected | Failed |" in text
    assert "$row.checkpoint.quarantined_records" in text
