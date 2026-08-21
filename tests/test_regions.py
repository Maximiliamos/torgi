from bankrotai.regions import REGION_DIRECTORY, normalize_region_code, region_label


def test_required_region_aliases_normalize_to_canonical_codes() -> None:
    expected = {
        "Ярославская область": "76",
        "Ярославская обл.": "76",
        "Москва": "77",
        "г Москва": "77",
        "Московская область": "50",
        "Рязанская область": "62",
        "Санкт-Петербург": "78",
        "Краснодарский край": "23",
        "Респ Татарстан": "16",
    }
    assert {value: normalize_region_code(value) for value in expected} == expected


def test_region_directory_has_unique_codes_names_and_aliases() -> None:
    codes = [region.code for region in REGION_DIRECTORY]
    names = [region.name.casefold() for region in REGION_DIRECTORY]
    assert len(REGION_DIRECTORY) == 89
    assert len(codes) == len(set(codes))
    assert len(names) == len(set(names))
    for region in REGION_DIRECTORY:
        assert normalize_region_code(region.code) == region.code
        assert normalize_region_code(region.name) == region.code


def test_region_label_is_user_facing_code_and_name() -> None:
    assert region_label("76") == "76 — Ярославская область"
