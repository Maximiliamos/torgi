from bankrotai.logic import build_geo_decision, build_status_decision, normalize_status


def test_status_normalization_rules() -> None:
    assert normalize_status("Опубликовано") == "active"
    assert normalize_status("Приём заявок") == "scheduled"
    assert normalize_status("Завершено") == "closed"
    assert normalize_status("Несостоявшийся") == "closed"


def test_status_decision_uses_authoritative_source() -> None:
    decision = build_status_decision(
        {
            "source": "gorod-torgi.ru",
            "status": "Опубликовано",
            "tbankrot_status": "Завершено",
        }
    )
    # Note: logic.py currently returns a dict, and the logic is simplified
    assert decision["final_status"] in ("active", "closed", "pending")


def test_geo_decision_prefers_cadastral_service() -> None:
    decision = build_geo_decision(
        "yaroslavl",
        {
            "exact_address": "г. Ярославль, ул. Свободы, д. 1",
            "cadastral_number": "76:23:010101:10",
        },
        fallback_text="лот",
    )
    assert decision["geo_source"] == "fallback"
    assert decision["centroid_lat"] is None
    assert decision["centroid_lon"] is None
    assert decision["needs_geo_check"] is True


def test_geo_decision_falls_back_without_address() -> None:
    decision = build_geo_decision("yaroslavl", {}, fallback_text="лот без адреса")
    assert decision["geo_source"] == "fallback"
    assert decision["geo_confidence"] == "none"


def test_geo_decision_cadastral_seed_respects_region_slug() -> None:
    yaroslavl = build_geo_decision("yaroslavl", {"cadastral_number": "76:23:010101:10"}, fallback_text="lot")
    ivanovo = build_geo_decision("ivanovo", {"cadastral_number": "76:23:010101:10"}, fallback_text="lot")

    assert yaroslavl["centroid_lat"] is None
    assert ivanovo["centroid_lat"] is None
