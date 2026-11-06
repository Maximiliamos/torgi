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


_RESUME = _ROOT / "scripts" / "resume-partial-standard-geo-map-no-vpn.ps1"


def test_partial_geo_map_resume_heredocs_are_valid_python() -> None:
    script = _RESUME.read_text(encoding="utf-8")
    snippets = re.findall(r"(?ms)^\s*\$(?:preflight|geoScript|mapScript) = @'\n(.*?)\n'@$", script)
    assert len(snippets) == 3
    for snippet in snippets:
        ast.parse(snippet, filename=str(_RESUME))


def test_partial_geo_map_resume_requires_exact_known_failure() -> None:
    script = _RESUME.read_text(encoding="utf-8")
    assert "-AcceptSixSourcePartial" in script
    assert 'run.status != "partial"' in script
    assert 'run.trigger_type != "manual_full"' in script
    assert 'set(by_name) != wanted | {"torgi-russia.ru"}' in script
    assert '"Torgi Russia public region filter was not applied:"' in script
    assert 'bad.items_inserted or bad.items_updated or bad.items_archived' in script
    assert "LotSyncRun.lease_expires_at > utc_now()" in script
    assert "Geocoding aborted by guarded backfill" in script
    assert "MAP_CURRENT=" in script
    assert "schedule_nationwide_lot_sync" not in script
    assert "MapDataset.created_at > run.finished_at" in script
    assert "do not repeat partial continuation" in script


_FULL = _ROOT / "scripts" / "run-standard-sources-geo-map-no-vpn.ps1"


def test_full_source_geo_map_operator_uses_full_non_archival_etp_contracts() -> None:
    script = _FULL.read_text(encoding="utf-8")
    snippets = re.findall(
        r"(?ms)^\s*\$(?:preflight|start|probe|geoScript|mapScript) = @'\n(.*?)\n'@$",
        script,
    )
    assert len(snippets) == 5
    for snippet in snippets:
        ast.parse(snippet, filename=str(_FULL))
    assert "[Parameter(Mandatory=$true)]" in script
    assert "x.max_batches != 500" in script
    assert "x.require_source_end is not True" in script
    assert "'MetsSyncFilters'" in script
    assert "'CdtrfSyncFilters'" in script
    assert "$row.pages -le 5" in script
    assert "reconcile_missing is not False" in script
