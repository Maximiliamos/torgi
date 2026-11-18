import base64
from datetime import datetime, timedelta, timezone
import hashlib
import json

import pytest

from bankrotai.services.nspd_evidence import nspd_result_from_receipt


NOW = datetime(2026, 11, 18, 7, tzinfo=timezone.utc)


def _receipt():
    body = json.dumps({"features": [{
        "type": "Feature", "properties": {"options": {"cad_num": "76:13:031301:92"}},
        "geometry": {"type": "Point", "coordinates": [39.4, 57.1]},
    }]}).encode()
    return {"provider": "nspd", "http_status": 200,
            "checked_at_utc": NOW.isoformat(), "cadastral_number": "76:13:031301:92",
            "response_sha256": hashlib.sha256(body).hexdigest(),
            "response_base64": base64.b64encode(body).decode()}


def test_exact_primary_response_can_be_normalized_without_another_request():
    result = nspd_result_from_receipt(_receipt(), now=NOW)
    assert result.cadastral_number == "76:13:031301:92"
    assert (result.lat, result.lon) == (57.1, 39.4)
    assert result.source == "nspd"


@pytest.mark.parametrize("change", ["identity", "sha", "expired", "future", "naive", "access_error"])
def test_invalid_primary_response_is_not_replayed(change):
    receipt = _receipt()
    if change == "identity":
        receipt["cadastral_number"] = "76:13:031301:999"
    elif change == "sha":
        receipt["response_sha256"] = "0" * 64
    elif change == "access_error":
        receipt["http_status"] = 429
    else:
        checked = {"expired": NOW - timedelta(hours=25), "future": NOW + timedelta(seconds=1),
                   "naive": NOW.replace(tzinfo=None)}[change]
        receipt["checked_at_utc"] = checked.isoformat()
    with pytest.raises(ValueError):
        nspd_result_from_receipt(receipt, now=NOW)
