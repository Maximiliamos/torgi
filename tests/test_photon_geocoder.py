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


def test_photon_rejects_wrong_city_and_uses_normalized_candidate() -> None:
    wrong = {
        "geometry": {"coordinates": [39.844022, 57.6103021]},
        "properties": {
            "city": "Ярославль", "state": "Ярославская область",
            "street": "Ленинградский проспект", "housenumber": "5А",
        },
    }
    correct = {
        "geometry": {"coordinates": [39.8828964, 57.6290494]},
        "properties": {
            "city": "Ярославль", "state": "Ярославская область",
            "street": "улица Свердлова", "housenumber": "5А",
        },
    }
    responses = []
    for feature in (wrong, correct):
        response = Mock()
        response.json.return_value = {"features": [feature]}
        response.raise_for_status.return_value = None
        responses.append(response)

    with patch("bankrotai.geo.requests.get", side_effect=responses) as request:
        result = PhotonGeocoder("http://photon:2322").geocode(
            "г. Ярославль, ул. Свердлова, д. 5а/17, Ярославская область"
        )

    assert request.call_count == 2
    assert result is not None
    assert result["centroid_lat"] == 57.6290494
    assert result["matched_address"].startswith("Ярославль")


def test_photon_rejects_only_result_from_another_city() -> None:
    response = Mock()
    response.json.return_value = {
        "features": [{
            "geometry": {"coordinates": [39.8517008, 57.3142477]},
            "properties": {
                "city": "Гаврилов-Ям", "state": "Ярославская область",
                "street": "улица Свердлова", "housenumber": "17",
            },
        }]
    }
    response.raise_for_status.return_value = None

    with patch("bankrotai.geo.requests.get", return_value=response):
        result = PhotonGeocoder("http://photon:2322").geocode(
            "г. Ярославль, ул. Свердлова, д. 5а/17, Ярославская область"
        )

    assert result is None
