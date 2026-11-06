"""Offline structural regression for the no-VPN source circuit recovery operator."""
from __future__ import annotations

import ast
from pathlib import Path
import re

_ROOT = Path(__file__).resolve().parents[1]
_PROBE = _ROOT / "scripts" / "diagnose-standard-source-circuits-no-vpn.ps1"


def test_source_circuit_probe_python_heredoc_parses() -> None:
    text = _PROBE.read_text(encoding="utf-8")
    matches = re.findall(r"(?ms)^\$python = @'\n(.*?)\n'@$", text)
    assert len(matches) == 1
    ast.parse(matches[0], filename=str(_PROBE))


def test_source_circuit_recovery_is_explicit_and_guarded() -> None:
    text = _PROBE.read_text(encoding="utf-8")
    assert "[switch]$ApplyVerifiedRecovery" in text
    assert "if recover:" in text
    assert "if not (all_proven and cooldowns_done):" in text
    assert "record_source_probe(session, name, success=True)" in text
    assert 'if name == "nistp.ru":' in text
    assert "independent_detail_proven(enriched)" in text
    assert "LotSyncRun.lease_expires_at > utc_now()" in text
    assert "commit()" in text  # Opt-in recovery health state, never source lot writes.
    assert "PROBING=" in text
