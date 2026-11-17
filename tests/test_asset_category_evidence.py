import pytest

from bankrotai.services.asset_category import classify_category
from bankrotai.services.map_property_eligibility import map_property_eligible


@pytest.mark.parametrize("title,description,expected", [
    ("АЗС, здание 62.4 м², ЗУ 3844 м²", "Для обслуживания автотранспорта", "commercial_building_with_land"),
    ("Земельный участок", "Подъезд по автомобильной дороге", "land"),
    ("Нежилое помещение", "Рядом автомобильная стоянка", "commercial_room"),
    ("Квартира", "В доме гараж и парковка автотранспорта", "apartment"),
    ("Машино-место", "На парковке автомобилей", "parking"),
    ("Volkswagen Caravelle 2008 г.в.", "Хранится в гараже на земельном участке", "car"),
    ("Грузовой автомобиль МАЗ", "Адрес хранения: нежилое помещение", "car"),
    ("Право требования к должнику", "Залог — квартира и земельный участок", "receivable"),
])
def test_primary_asset_is_not_replaced_by_incidental_description(title, description, expected):
    assert classify_category(title, description) == expected


def test_singular_receivable_cannot_be_published_as_collateral_real_estate():
    assert not map_property_eligible({"category": "receivable", "title": "Право требования",
                                      "description": "Залог — квартира и земельный участок"})


@pytest.mark.parametrize("title", ["Грузовой автомобиль МАЗ", "Право требования, залог — квартира"])
def test_generic_source_property_label_does_not_override_primary_object(title):
    assert not map_property_eligible({"category": "real_estate", "title": title})
