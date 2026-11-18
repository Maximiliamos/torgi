import base64
from datetime import datetime, timezone
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sys

import pytest


@pytest.mark.parametrize("has_description", [False, True])
def test_primary_receipts_distinguish_literal_description_from_title_fallback(monkeypatch, capsys, has_description):
    path = Path(__file__).resolve().parents[1] / "scripts" / "prepare_verified_gov_repair.py"
    spec = importlib.util.spec_from_file_location("prepare_gov_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    payload = {"noticeNumber": "23000054230000000401", "lotNumber": 9,
               "lotName": "Нежилое помещение", "priceMin": 1000, "lotStatus": "PUBLISHED"}
    if has_description:
        payload["lotDescription"] = "Нежилое помещение площадью 100 кв. м."
    body = json.dumps(payload).encode()
    receipt = {"lot_id": 1, "external_id": "torgi_gov:23000054230000000401_9",
               "snapshot_base64": base64.b64encode(body).decode(),
               "sha256": hashlib.sha256(body).hexdigest(),
               "checked_at_utc": datetime.now(timezone.utc).isoformat()}
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps([receipt])))
    module.main()
    item = json.loads(capsys.readouterr().out)["items"][0]
    fields = item["verification"]["observed_fields"]
    assert "title" in fields
    assert ("description" in fields) == has_description
    assert ("category" in item["verification"]["derived_fields"]) == has_description
    if has_description:
        assert item["normalized"]["category"] == "commercial_room"
