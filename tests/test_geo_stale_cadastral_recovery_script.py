from pathlib import Path


def test_stale_cadastral_recovery_script_is_bounded_and_dry_run_by_default() -> None:
    text = Path("scripts/geo_stale_cadastral_recovery.py").read_text(encoding="utf-8")

    assert 'action="store_true"' in text
    assert "1 <= len(ids) <= 20" in text
    assert "_search_nspd_geoportal" in text
    assert "live_nspd_identity_mismatch" in text
    assert "repair_stale" not in text
    assert "recover_stale_cadastral_no_match" in text
    assert "session.rollback()" in text
    assert "session.commit()" in text
