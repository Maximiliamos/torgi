import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "cloudflare_direct_cutover.py"
SPEC = importlib.util.spec_from_file_location("cloudflare_direct_cutover", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
DIRECT_IP = MODULE.DIRECT_IP
desired_records = MODULE.desired_records


def test_switch_points_only_managed_hosts_to_direct_regru() -> None:
    records = desired_records("switch")
    assert {item["name"] for item in records} == {
        "dezster.ru",
        "www.dezster.ru",
        "api.dezster.ru",
    }
    assert all(item["type"] == "A" for item in records)
    assert all(item["content"] == DIRECT_IP for item in records)
    assert all(item["proxied"] is False for item in records)


def test_rollback_restores_previous_edge_records() -> None:
    records = desired_records("rollback")
    assert {(item["name"], item["type"]) for item in records} == {
        ("dezster.ru", "A"),
        ("dezster.ru", "AAAA"),
        ("www.dezster.ru", "A"),
        ("www.dezster.ru", "AAAA"),
        ("api.dezster.ru", "CNAME"),
    }
    assert all(item["proxied"] is True for item in records)
