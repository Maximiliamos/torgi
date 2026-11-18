from unittest.mock import Mock

import pytest

from bankrotai.geo import CadastralGeocoder, CadastralObjectResult, PhotonGeocoder, validate_geocoding_result
from bankrotai.services.geo_address_candidates import is_region_only_address


@pytest.mark.parametrize("address", [
    "обл. Ярославская", "область Ярославская, Ярославская область",
    "Россия, Ярославская обл.", "Республика Коми", "Краснодарский край", "г. Москва",
])
def test_region_labels_do_not_identify_a_property(address):
    assert is_region_only_address(address)


@pytest.mark.parametrize("address", [
    "Ярославская область, село Флоровское, улица Цветочная, дом 4",
    "г. Москва, Ленинградское шоссе, дом 228", "Ярославская область, Мышкинский район",
    "Ярославская область, 76:07:101701:72",
])
def test_property_or_district_information_is_not_classified_as_only_region(address):
    assert not is_region_only_address(address)


def test_region_query_never_requests_an_arbitrary_photon_house(monkeypatch):
    request = Mock(side_effect=AssertionError("Coarse query must not contact provider"))
    monkeypatch.setattr("bankrotai.geo.requests.get", request)
    assert PhotonGeocoder("http://photon:2322").geocode("область Ярославская, Ярославская область") is None
    result = CadastralGeocoder().search_by_address("обл. Ярославская")
    assert result.lat is None and result.error == "region_only_address"
    request.assert_not_called()


def test_cached_precise_house_from_region_only_query_is_rejected():
    result = CadastralObjectResult(query="область Ярославская, Ярославская область",
        address="Ярославль, улица Победы, 20А, Ярославская область",
        source="photon", confidence="high", lat=57.6360203, lon=39.875625)
    assert validate_geocoding_result(result, cadastral_number="76:07:101701:72",
        address="обл. Ярославская", region_name="Ярославская область", region_code="76") == (False, "region_only_address")


def test_exact_cadastral_result_remains_valid_with_coarse_source_address():
    result = CadastralObjectResult(query="76:07:101701:72", cadastral_number="76:07:101701:72",
        address="Ярославская область, село Флоровское", source="nspd", confidence="high",
        lat=57.8, lon=38.4)
    assert validate_geocoding_result(result, cadastral_number="76:07:101701:72",
        address="обл. Ярославская", region_name="Ярославская область", region_code="76")[0]
