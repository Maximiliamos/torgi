import pytest

from bankrotai.services.map_property_eligibility import map_property_eligible


@pytest.mark.parametrize("values", [
    {"category": "car", "title": "Volkswagen Caravelle 2008"},
    {"category": "equipment", "title": "Оборудование в нежилом здании"},
    {"category": "receivables", "title": "Право требования, залог — квартира"},
    {"category": "commercial_room", "title": "Право заключения договора аренды помещения"},
    {"category": "land", "title": "Право аренды земельного участка"},
    {"category": "land", "title": "Лот №1 (первичные) Продажа права аренды земельного участка"},
    {"category": "land", "title": "Лот №2: Аренда земельного участка"},
    {"category": "commercial_room", "title": "Лот 3. Право аренды помещения"},
    {"category": "land", "title": "Земельный участок", "auction_type": "Аренда"},
    {"category": "land", "title": "Земельный участок", "official_transaction": "RENT"},
    {"category": "land", "title": "Земельный участок", "official_transaction": '{"code":"RENT"}'},
    {"category": "apartment", "title": "Старая карточка квартиры", "source_sale_eligible": False},
])
def test_legacy_nonproperty_projections_cannot_bypass_ingestion_filter(values):
    assert not map_property_eligible(values)


@pytest.mark.parametrize("values", [
    {"category": "apartment", "title": "Квартира"},
    {"category": "commercial_room", "title": "Лот №1 (первичные) Нежилое помещение",
     "description": "Продажа помещения; обременение: действующий арендатор"},
    {"category": "land", "title": "Земельный участок"},
    {"category": "land", "title": "Земельный участок", "official_transaction": "SALE"},
    {"category": "commercial_room", "title": "Нежилое помещение",
     "description": "Продажа помещения; обременение: действующий арендатор"},
])
def test_actual_sales_are_retained(values):
    assert map_property_eligible(values)


@pytest.mark.parametrize("title", [
    "Лот №1 (первичные) Продажа права аренды земельного участка",
    "Лот №2: Аренда земельного участка",
    "Лот 3. Право аренды помещения",
])
def test_rejected_rental_title_records_positive_exclusion_evidence_for_old_projection(title):
    from types import SimpleNamespace
    from bankrotai.services.source_eligibility_evidence import exclusion_reason
    assert exclusion_reason(SimpleNamespace(title=title,raw_data={}))=="explicit_rental_title"


@pytest.mark.parametrize("source", ["sistematorg.com", "aukcioncenter.ru"])
def test_trade_only_legacy_identifier_cannot_certify_an_individual_property(source):
    assert not map_property_eligible({"source_system":source,"external_id":source+":12345",
        "category":"land","title":"Земельный участок"})
    assert map_property_eligible({"source_system":source,"external_id":source+":12345:2",
        "category":"land","title":"Земельный участок"})
