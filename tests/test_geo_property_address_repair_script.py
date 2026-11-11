from pathlib import Path


def test_property_address_repair_script_is_bounded_and_dry_run_by_default() -> None:
    text = Path("scripts/geo_property_address_repair.py").read_text(encoding="utf-8")

    assert 'action="store_true"' in text
    assert "1 <= len(ids) <= 100" in text
    assert "repair_property_addresses" in text
    assert "session.rollback()" in text
    assert "session.commit()" in text
    assert '"database_writes": result["updated"] if args.apply else 0' in text
