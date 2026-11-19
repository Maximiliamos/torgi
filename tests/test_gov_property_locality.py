import pytest

from bankrotai.geo import CadastralObjectResult, expected_locality_name, validate_geocoding_result
from bankrotai.scrapers import TorgiGovClient
from bankrotai.services.torgi_gov_normalization import normalize_torgi_gov_property_address


@pytest.mark.parametrize('address,expected', [
    ('обл. Московская, м.о. Истра, г. Истра Российская Федерация, Московская область, муниципальный округ Истра, д Кореньки', 'кореньки'),
    ('обл. Московская, м.о. Шаховская, д. Рождествено Российская Федерация, Московская область, м.о. Шаховская, д Рождествено', 'рождествено'),
    ('Московская область, м.о. Лотошино, д Кельи, з/у 22Б', 'кельи'),
    ('Московская область, п Малое Ушаково', 'малое ушаково'),
    ('Московская область, с Новое', 'новое'),
    ('Московская область, посёлок Северный', 'северный'),
    ('г. Ярославль, улица Ленина, д 12', 'ярославль'),
    ('г. Ярославль, улица Ленина, д.12', 'ярославль'),
    ('Московская область, г.о. Истра', ''),
    ('Московская область, с.п. Северное', ''),
])
def test_specific_locality_abbreviations_are_not_houses_or_municipalities(address, expected):
    assert expected_locality_name(address) == expected


@pytest.mark.parametrize('observed,valid', [('д Кореньки', True), ('д Коренево', False)])
def test_exact_cadastral_geometry_still_rejects_a_genuinely_different_locality(observed, valid):
    address = 'обл. Московская, м.о. Истра, г. Истра Российская Федерация, Московская область, муниципальный округ Истра, д Кореньки'
    result = CadastralObjectResult(query='50:08:0090215:1034', cadastral_number='50:08:0090215:1034',
        source='nspd', confidence='high', address='Московская область, ' + observed, lat=55.978876, lon=36.484258)
    passed, reason = validate_geocoding_result(result, cadastral_number=result.cadastral_number,
        address=address, region_name='Московская область', region_code='50')
    assert passed is valid
    if not valid:
        assert reason == 'locality_name_mismatch'


def test_official_normalizer_retains_the_raw_address_and_uses_the_cadastral_property_tail():
    original = 'обл. Московская, м.о. Истра, г. Истра Российская Федерация, Московская область, муниципальный округ Истра, д Кореньки'
    lot = TorgiGovClient()._normalize_api_lot({'id': '21000012550000001058_1',
        'lotName': 'Земельный участок 50:08:0090215:1034', 'estateAddress': original})
    assert lot.address == 'Российская Федерация, Московская область, муниципальный округ Истра, д Кореньки'
    assert lot.raw_data['raw']['estateAddress'] == original


@pytest.mark.parametrize('address,cad', [
    ('Московская область, г. Истра, ул. Почтовая, д. 1 Российская Федерация, Московская область, д Кореньки', '50:08:0090215:1034'),
    ('Московская область, г. Истра Российская Федерация, Московская область, д Кореньки', '76:01:010101:1'),
    ('Московская область, г. Истра Российская Федерация, Московская область, д Кореньки', None),
])
def test_numbered_or_region_conflicting_or_unbound_prefix_is_not_discarded(address, cad):
    assert normalize_torgi_gov_property_address(address, cadastral_number=cad) == address
