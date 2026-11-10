from bankrotai.regions import (
    REGION_DIRECTORY,
    normalize_region_code,
    region_code_from_cadastral_number,
    region_code_from_cadastral_prefix,
    region_label,
)


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


def test_cadastral_district_codes_are_not_treated_as_modern_subject_codes() -> None:
    expected = {
        "80": "75",  # Aginsky -> Zabaykalsky Krai
        "81": "59",  # Komi-Permyak -> Perm Krai
        "82": "41",  # Koryak -> Kamchatka Krai
        "84": "24",  # Taimyr -> Krasnoyarsk Krai
        "85": "38",  # Ust-Orda -> Irkutsk Oblast
        "88": "24",  # Evenki -> Krasnoyarsk Krai
        "90": "82",  # Crimea cadastral district
        "91": "92",  # Sevastopol cadastral district
        "93": "80",  # Donetsk cadastral district
        "94": "84",  # Kherson cadastral district
        "95": "81",  # Luhansk cadastral district
        "96": "85",  # Zaporizhzhia cadastral district
    }
    assert {code: region_code_from_cadastral_prefix(code) for code in expected} == expected
    assert region_code_from_cadastral_number("85:01:091106:59") == "38"
    assert region_code_from_cadastral_number("81:07:0112002:74") == "59"
