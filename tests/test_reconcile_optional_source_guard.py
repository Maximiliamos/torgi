"""BAT-309: disabled sources must not fail reconciliation or claim coverage."""

from pathlib import Path


WORKFLOW = (
    Path(__file__).resolve().parents[1]
    / ".github/workflows/phase3-lite-full-reconcile.yml"
).read_text(encoding="utf-8")


def test_full_reconcile_validates_the_runtime_configured_source_set() -> None:
    assert "configured=[x.source_id for x in _unpaused_source_specs(default_source_specs())]" in WORKFLOW
    assert "$expectedSources = @($state.configured_sources)" in WORKFLOW
    assert "$missingSources = @($expectedSources | Where-Object" in WORKFLOW
    assert "$incompleteRows = @($rows | Where-Object" in WORKFLOW


def test_optional_torgi_russia_is_only_required_when_configured() -> None:
    start = WORKFLOW.index("if ('torgi-russia.ru' -in $expectedSources) {")
    stop = WORKFLOW.index("Write-Output 'Torgi Russia is not configured", start)
    conditional = WORKFLOW[start:stop]
    assert "Where-Object { $_.source -eq 'torgi-russia.ru' }" in conditional
    assert "[int64]$torgiRussia.items_seen -le 0" in conditional
    assert 'throw "Configured Torgi Russia production parser returned no lots"' in conditional
    assert "no parser coverage claimed" in WORKFLOW
    assert 'throw "Torgi Russia production parser returned no lots"' not in WORKFLOW
