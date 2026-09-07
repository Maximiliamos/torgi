from scripts.cloudflare_direct_cutover import DIRECT_IP, desired_records


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
