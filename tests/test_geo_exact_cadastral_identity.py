"""Provider feature ordering must never substitute for cadastral identity."""
from bankrotai.geo import CadastralGeocoder


def test_pkk_matches_requested_number_not_first_feature():
    geocoder = CadastralGeocoder()
    result = geocoder._parse_pkk_feature({
        "features": [
            {"attrs": {"cn": "76:02:022201:39"}, "center": {"x": 39.2, "y": 57.2}},
            {"attrs": {"cn": "76:02:022201:38"}, "center": {"x": 39.8, "y": 57.6}},
        ]
    }, "76:02:022201:38", "land_plot")
    assert result is not None
    assert result.cadastral_number == "76:02:022201:38"
    assert result.lat == 57.6
    assert result.lon == 39.8


def test_pkk_rejects_nonmatching_feature_instead_of_centroid():
    geocoder = CadastralGeocoder()
    result = geocoder._parse_pkk_feature({
        "features": [
            {"attrs": {"cn": "76:02:022201:39"}, "center": {"x": 39.2, "y": 57.2}}
        ]
    }, "76:02:022201:38", "land_plot")
    assert result is None


def test_nspd_rejects_free_text_match_and_missing_identity():
    geocoder = CadastralGeocoder()
    features = [{
        "properties": {
            "options": {"cad_num": "76:02:022201:39"},
            "description": "Adjacent to plot 76:02:022201:38",
        },
        "geometry": {"type": "Point", "coordinates": [39.2, 57.2]},
    }]
    assert geocoder._pick_nspd_feature(features, "76:02:022201:38") is None
    assert geocoder._pick_nspd_feature([{"properties": {}}], "76:02:022201:38") is None


def test_nspd_chooses_second_matching_structured_feature():
    geocoder = CadastralGeocoder()
    wrong = {"properties": {"options": {"cad_num": "76:02:022201:39"}}}
    expected = {"properties": {"options": {"cad_num": "76:02:022201:38"}}}
    assert geocoder._pick_nspd_feature(
        [wrong, expected], "76:02:022201:38"
    ) is expected
