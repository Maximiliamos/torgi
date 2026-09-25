from bankrotai.region_sanity import (
    REGION_SANITY_ENVELOPES,
    coordinate_matches_region_sanity,
    uncovered_canonical_region_codes,
)
from bankrotai.regions import REGION_DIRECTORY


def test_every_canonical_region_has_a_gross_outlier_envelope() -> None:
    assert uncovered_canonical_region_codes() == set()
    assert set(REGION_SANITY_ENVELOPES) == {region.code for region in REGION_DIRECTORY}


def test_known_valid_extrema_are_retained_with_conservative_margin() -> None:
    samples = (
        ("39", 54.7, 20.5),   # Kaliningrad
        ("41", 56.0, 160.0),  # Kamchatka
        ("65", 46.9, 142.7),  # Sakhalin
        ("14", 62.0, 129.7),  # Yakutia
        ("50", 55.9, 35.3),   # western Moscow region
        ("25", 43.1, 131.9),  # Primorsky krai
    )
    for code, lat, lon in samples:
        assert coordinate_matches_region_sanity(lat, lon, code)


def test_chukotka_supports_both_sides_of_antimeridian() -> None:
    assert coordinate_matches_region_sanity(66.0, 177.0, "87")
    assert coordinate_matches_region_sanity(66.0, -169.0, "87")
    assert not coordinate_matches_region_sanity(66.0, 120.0, "87")


def test_known_cross_country_outliers_are_rejected() -> None:
    samples = (
        ("02", 55.0, 127.5),
        ("16", 55.0, 133.0),
        ("22", 55.0, 143.0),
        ("52", 56.0, 124.0),
        ("50", 55.5, 105.0),
    )
    for code, lat, lon in samples:
        assert not coordinate_matches_region_sanity(lat, lon, code)


def test_unknown_region_is_not_rejected_by_guesswork() -> None:
    assert coordinate_matches_region_sanity(0.0, 0.0, None)
    assert coordinate_matches_region_sanity(0.0, 0.0, "unknown")
