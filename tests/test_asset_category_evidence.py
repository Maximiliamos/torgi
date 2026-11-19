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
    ("Гараж для автомобиля с земельным участком", "", "land"),
    ("Гараж в ГСК Автомобиль 1", "", "parking"),
    ("Нежилое здание (гараж легкового автомобиля) с земельным участком", "", "commercial_building_with_land"),
    ("Земельный участок со стоянкой на 5 автомобилей", "", "land"),
    ("ЖБЦ пл. № 2 Линия концевого участка № 2 СПР-20 (2030073), количество: 1 шт.", "", "equipment"),
    ("Производственная линия по обработке древесины", "Расположена в здании цеха", "equipment"),
    ("Здание цеха с производственной линией", "", "commercial_building"),
    ("Поручение №2438/Т. Автомобиль Форд. Место хранения — гараж", "", "car"),
    ("Жилое помещение (комната)", "", "living"),
    ("Комната площадью 12 кв.м.", "Доступ автомобильного транспорта", "real_estate"),
    ("Оборудование", "Хранится в нежилом здании", "equipment"),
    ("Оборудованное нежилое помещение", "", "commercial_room"),
    ("Количество и породный состав древесины: сосна, 120 куб. м", "Земельный участок 76:13:031301:92", "commodity"),
    ("Древесина на корню", "Расположена на земельном участке", "commodity"),
    ("Право заключения договора купли-продажи лесных насаждений", "Лесной участок", "commodity"),
    ("Земельный участок лесного фонда", "Количество древесины на участке", "land"),
    ("Здание деревообрабатывающего цеха", "Заготовка древесины", "commercial_building"),
])
def test_primary_asset_is_not_replaced_by_incidental_description(title, description, expected):
    assert classify_category(title, description) == expected


def test_singular_receivable_cannot_be_published_as_collateral_real_estate():
    assert not map_property_eligible({"category": "receivable", "title": "Право требования",
                                      "description": "Залог — квартира и земельный участок"})


@pytest.mark.parametrize("title", [
    "Грузовой автомобиль МАЗ", "Право требования, залог — квартира",
    "Количество и породный состав древесины: сосна, земельный участок 76:13:031301:92",
    "Лесные насаждения на земельном участке",
])
def test_generic_source_property_label_does_not_override_primary_object(title):
    assert not map_property_eligible({"category": "real_estate", "title": title})


@pytest.mark.parametrize("title", ["Гараж для автомобиля с земельным участком", "Гараж в ГСК Автомобиль 1"])
def test_vehicle_word_in_property_title_is_not_a_vehicle_lot(title):
    assert map_property_eligible({"category": "real_estate", "title": title})
