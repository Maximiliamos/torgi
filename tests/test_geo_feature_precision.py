from unittest.mock import Mock, patch

import pytest

from bankrotai.geo import CadastralObjectResult, PhotonGeocoder, validate_geocoding_result
from bankrotai.services.geo_feature_precision import property_number, whole_locality_match


@pytest.mark.parametrize(('address', 'expected'), [
    ('г. Мышкин, гаражный комплекс, 2', '2'),
    ('г. Ярославль, улица Свердлова, 5а/17', '5a/17'),
    ('д. Губцево, дом 12', '12'),
    ('СНТ Север, участок № 15', '15'),
    ('152830, д. Губцево', ''),
    ('г. Ярославль, улица 8 Марта', ''),
    ('76:07:012501:590', ''),
])
def test_property_number_is_not_postcode_cadastre_or_street_name(address, expected):
    assert property_number(address) == expected


def test_locality_is_not_a_county_prefix():
    assert not whole_locality_match('Ростов', 'Ростовский район, деревня Поречье')
    assert whole_locality_match('Ростов', 'г. Ростов, Ярославская область')


def test_photon_rejects_different_bare_house_number():
    response = Mock()
    response.json.return_value = {'features':[{'geometry':{'coordinates':[38.45,57.78]},
        'properties':{'city':'Мышкин','housenumber':'4','street':'Успенская площадь'}}]}
    with patch('bankrotai.geo.requests.get', return_value=response):
        assert PhotonGeocoder('http://photon:2322').geocode('г. Мышкин, гаражный комплекс, 2') is None


def test_village_centroid_is_navigation_only_and_cannot_be_persisted_as_property():
    response = Mock()
    response.json.return_value = {'features':[{'geometry':{'coordinates':[39.7,57.7]},
        'properties':{'name':'Губцево','osm_key':'place','state':'Ярославская область'}}]}
    with patch('bankrotai.geo.requests.get', return_value=response):
        match = PhotonGeocoder('http://photon:2322').geocode('деревня Губцево, Ярославская область')
    assert match is not None and match['geo_confidence'] == 'low'
    old = CadastralObjectResult(query='деревня Губцево, Ярославская область',
        address='Губцево, Ярославская область', lat=57.7, lon=39.7, source='photon', confidence='high')
    assert validate_geocoding_result(old,cadastral_number=None,address=old.query,
        region_name='Ярославская область',region_code='76') == (False,'property_locator_missing')


def test_cached_different_number_and_slash_suffix_are_rejected():
    for query, matched in [('г. Мышкин, гаражный комплекс, 2','Мышкин, Успенская площадь, 4'),
                           ('г. Ярославль, дом 5а/17','Ярославль, дом 5а/18')]:
        old=CadastralObjectResult(query=query,address=matched,lat=57.7,lon=39.7,source='photon',confidence='high')
        assert validate_geocoding_result(old,cadastral_number=None,address=query,
            region_name='Ярославская область',region_code='76') == (False,'property_number_mismatch')


def test_exact_cadastral_geometry_does_not_require_a_house_number():
    exact=CadastralObjectResult(query='76:07:012501:590',cadastral_number='76:07:012501:590',
        address='г. Мышкин, Ярославская область',lat=57.78,lon=38.45,source='nspd',confidence='high')
    assert validate_geocoding_result(exact,cadastral_number=exact.cadastral_number,
        address='г. Мышкин',region_name='Ярославская область',region_code='76')[0]
