from pathlib import Path


def test_bidexpert_detail_repair_script_is_bounded_and_dry_run_by_default() -> None:
    text = Path("scripts/geo_bidexpert_detail_repair.py").read_text(encoding="utf-8")

    assert 'action="store_true"' in text
    assert "1 <= len(ids) <= 20" in text
    assert "client.fetch_lot_detail" in text
    assert "repair_source_details_for_geo" in text
    assert "session.rollback()" in text
    assert "session.commit()" in text
