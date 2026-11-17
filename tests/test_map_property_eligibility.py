import pytest

from bankrotai.services.map_property_eligibility import map_property_eligible


@pytest.mark.parametrize("values", [
    {"category": "car", "title": "Volkswagen Caravelle 2008"},
    {"category": "equipment", "title": "Оборудование в нежилом здании"},
    {"category": "receivables", "title": "Право требования, залог — квартира"},
    {"category": "commercial_room", "title": "Право заключения договора аренды помещения"},
])
def test_legacy_nonproperty_projections_cannot_bypass_ingestion_filter(values):
    assert not map_property_eligible(values)


@pytest.mark.parametrize("values", [
    {"category": "apartment", "title": "Квартира"},
    {"category": "land", "title": "Земельный участок"},
    {"category": "commercial_room", "title": "Нежилое помещение",
     "description": "Продажа помещения; обременение: действующий арендатор"},
])
def test_actual_sales_are_retained(values):
    assert map_property_eligible(values)
