from unittest.mock import Mock, patch

from bankrotai.geo import CadastralGeocoder, PhotonGeocoder


def test_photon_returns_matching_russian_address() -> None:
    response = Mock()
    response.json.return_value = {"features": [{"geometry": {"coordinates": [39.884, 57.626]}, "properties": {"city": "Ярославль", "street": "улица Свердлова", "housenumber": "5а/17"}}]}
    response.raise_for_status.return_value = None
    with patch("bankrotai.geo.requests.get", return_value=response):
        result = PhotonGeocoder("http://photon:2322").geocode("г. Ярославль, ул. Свердлова, д. 5а/17")
    assert result is not None
    assert result["centroid_lat"] == 57.626


def test_address_chain_prefers_photon_and_does_not_call_nominatim(monkeypatch) -> None:
    monkeypatch.setattr("bankrotai.geo.PHOTON_GEOCODER.geocode", lambda _address: {"centroid_lat": 57.626, "centroid_lon": 39.884, "geo_confidence": "high", "trace_reason": "local"})
    nominatim = Mock()
    monkeypatch.setattr("bankrotai.geo.NOMINATIM_GEOCODER.geocode", nominatim)
    result = CadastralGeocoder().search_by_address("г. Ярославль, ул. Свердлова, д. 5а/17")
    assert result.source == "photon"
    nominatim.assert_not_called()
