"""BAT-309: P11 failure must include per-endpoint evidence without relaxing soak."""

from pathlib import Path


P11 = (
    Path(__file__).resolve().parents[1] / ".github/workflows/p11-production-acceptance.yml"
).read_text(encoding="utf-8")


def test_p11_writes_partial_soak_evidence_before_error() -> None:
    start = P11.index("      - name: Read-only production soak")
    end = P11.index("      - name: Upload P11 evidence", start)
    soak = P11[start:end]
    persist = soak.index('$samples | ConvertTo-Json -Depth 5 | Set-Content')
    fail = soak.index('throw "Production health failed during soak:')
    assert persist < fail
    assert 'live_exception_type = $liveError' in soak
    assert 'ready_exception_type = $readyError' in soak
    assert 'if (-not $liveOk -or -not $readyOk)' in soak
    assert 'if ($freeGb -lt 10)' in soak


def test_soak_failed_artifact_is_still_uploaded() -> None:
    assert "      - name: Upload P11 evidence" in P11
    assert "if: always()" in P11
    assert r"p11-soak.json" in P11
