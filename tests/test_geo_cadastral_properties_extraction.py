"""BAT-308: cadastral property normalization preserves its legacy GEO contract."""

from bankrotai import geo
from bankrotai.services import geo_cadastral_properties as props


def test_legacy_geo_symbols_are_same_objects() -> None:
    assert geo.CADASTRAL_RE is props.CADASTRAL_RE
    assert geo.normalize_nspd_props is props.normalize_nspd_props
    assert geo.normalize_pkk_attrs is props.normalize_pkk_attrs


def test_nspd_properties_prefer_nested_options_and_trusted_number() -> None:
    source = {
        "address": "outdated address",
        "options": {
            "readable_address": "г. Москва, ул. Лесная, 5",
            "cadNum": "77:01:0001001:42",
            "area": 1500,
            "name": "77:01:0001001:42",
        },
    }
    result = geo.normalize_nspd_props(source, "77:01:0001001:100")
    assert result["Кадастровый номер"] == "77:01:0001001:42"
    assert result["Адрес"] == "г. Москва, ул. Лесная, 5"
    assert result["Площадь общая"] == 1500
    assert result["Наименование"] is None


def test_invalid_observed_cadastre_does_not_replace_requested_number() -> None:
    result = props.normalize_nspd_props({"cadNum": "not-a-cadastre"}, "50:11:0001001:17")
    assert result["Кадастровый номер"] == "50:11:0001001:17"


def test_pkk_legacy_aliases_and_default_object_type() -> None:
    result = props.normalize_pkk_attrs(
        {"cn": "50:11:0001001:17", "cad_cost_value": "123456", "address": "с. Озёрное"},
        "50:11:0001001:17",
        "land_plot",
    )
    assert result["Вид объекта недвижимости"] == "Земельный участок"
    assert result["Кадастровый номер"] == "50:11:0001001:17"
    assert result["Кадастровая стоимость"] == "123456"
    assert result["Адрес"] == "с. Озёрное"
    building = props.normalize_pkk_attrs({}, "77:01:0001001:42", "building")
    assert building["Вид объекта недвижимости"] == "Здание"
