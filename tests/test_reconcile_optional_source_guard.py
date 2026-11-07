"""BAT-309: full production acceptance must be exactly the approved seven sources."""

from pathlib import Path


WORKFLOW = (
    Path(__file__).resolve().parents[1]
    / ".github/workflows/phase3-lite-full-reconcile.yml"
).read_text(encoding="utf-8")


APPROVED = (
    "torgi.gov.ru",
    "lot-online.ru",
    "torgi-russia.ru",
    "bidexpert.ru",
    "m-ets.ru",
    "cdtrf.ru",
    "nistp.ru",
)


def test_full_reconcile_requires_exact_approved_runtime_source_set() -> None:
    assert "approved_sources" in WORKFLOW
    assert "_unpaused_source_specs(default_source_specs())" in WORKFLOW
    assert "Refusing partial full reconciliation" in WORKFLOW
    assert "$expectedSources = @($state.configured_sources)" in WORKFLOW
    assert "$missingSources = @($expectedSources | Where-Object" in WORKFLOW
    assert "$incompleteRows = @($rows | Where-Object" in WORKFLOW
    for source in APPROVED:
        assert source in WORKFLOW


def test_torgi_russia_remains_required_and_must_return_real_lots() -> None:
    start = WORKFLOW.index("if ('torgi-russia.ru' -in $expectedSources) {")
    stop = WORKFLOW.index("Write-Output 'Torgi Russia is not configured", start)
    conditional = WORKFLOW[start:stop]
    assert "Where-Object { $_.source -eq 'torgi-russia.ru' }" in conditional
    assert "[int64]$torgiRussia.items_seen -le 0" in conditional
    assert 'throw "Configured Torgi Russia production parser returned no lots"' in conditional
    # The fallback message remains for defensive diagnostics, but the preflight
    # now refuses production acceptance before a missing approved source can run.
    assert "Refusing partial full reconciliation" in WORKFLOW
