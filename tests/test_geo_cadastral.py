from bankrotai.geo import CadastralGeocoder


def test_parse_pkk_feature_returns_result() -> None:
    geocoder = CadastralGeocoder()
    result = geocoder._parse_pkk_feature(
        {
            "features": [
                {
                    "attrs": {
                        "cn": "76:23:010101:15008",
                        "address": "г. Ярославль, Ленинградский пр-т, д. 105",
                        "type_name": "Здание",
                        "area_value": "123.4",
                    },
                    "center": {"x": 39.8845, "y": 57.6261},
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [
                            [
                                [39.884, 57.626],
                                [39.885, 57.626],
                                [39.885, 57.627],
                                [39.884, 57.626],
                            ]
                        ],
                    },
                }
            ]
        },
        "76:23:010101:15008",
        "building",
    )

    assert result is not None
    assert result.source == "pkk"
    assert result.cadastral_number == "76:23:010101:15008"
    assert result.lat == 57.6261
    assert result.lon == 39.8845
    assert result.has_boundary is True
