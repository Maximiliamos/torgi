"""P0 offline regressions; no production database or provider access."""
from __future__ import annotations

import json
from unittest.mock import Mock

from scripts.geo_p0_readonly import classify_failure, summarize_rows
import scripts.geo_p0_provider_probe as probes


def row(
    pk: int, *, source: str = "one.test", status: str = "active",
    address=None, cad=None, title="", description="",
    failure_status=None, failure_message=None,
):
    return (
        pk, source, None, None, status, address, cad, title,
        description, failure_status, failure_message,
    )


def test_p0_full_population_exclusive_buckets_and_no_raw_leaks():
    rows = [
        row(
            1,
            description="Земля, адрес: Ленинградская область, д Прологи, "
                        "кадастровый номер: 47:22:0325001:373",
        ),
        row(2, source="two.test", description="нет подробностей"),
        row(
            3, cad="77:09:0004009:5974",
            description="Квартира, адрес: г. Москва, проспект Ленинградский, дом 52",
        ),
        row(
            4, address="Москва, ул. Тверская, д. 1",
            cad="77:01:0001001:12", failure_status="network_wait",
            failure_message=json.dumps({
                "attempts": [
                    {"source": "photon", "reason": "provider_protocol", "operational": True},
                ],
            }),
        ),
        row(
            5, address="Костромская область, ул. Октябрьская, д. 46",
            failure_status="deferred_validation",
        ),
        row(
            6, address="г. Москва, ул. Садовая, д. 10",
            failure_status="deferred_no_match",
            failure_message=json.dumps({
                "attempts": [{"source": "nspd_cadastral", "reason": "no_coordinates"}],
            }),
        ),
        row(7, status="closed", address="Москва, ул. Тверская, д. 1"),
    ]
    result = summarize_rows(rows)
    assert result["nonarchived_unduplicated_unmapped"] == 7
    assert result["active_or_scheduled_unmapped"] == 6
    assert result["nonactive_unmapped"] == 1
    assert sum(result["primary_causes_active_exclusive"].values()) == 6
    assert result["primary_causes_active_exclusive"] == {
        "source_extraction_gap_candidate": 1,
        "source_detail_required": 1,
        "source_address_extraction_gap_candidate": 1,
        "provider_operational": 1,
        "validation_rejected": 1,
        "unconfirmed_provider_no_match": 1,
    }
    assert result["by_source"]["two.test"]["active"] == 1
    assert result["provider_attempt_sources"] == {"photon": 1, "nspd_cadastral": 1}
    output = json.dumps(result, ensure_ascii=False)
    assert "Садовая" not in output and "Тверская" not in output
    assert "Ленинградский" not in output and "Прологи" not in output


def test_p0_network_failure_never_masquerades_as_semantic_miss():
    result, sources, reasons = classify_failure(
        "deferred_no_match",
        json.dumps({"attempts": [
            {"source": "photon", "reason": "connection_error", "operational": True},
        ]}),
    )
    assert result == "provider_operational"
    assert sources == ["photon"]
    assert reasons == ["connection_error"]


def test_p0_probe_without_photon_endpoint_makes_zero_network_calls(monkeypatch):
    monkeypatch.delenv("PHOTON_BASE_URL", raising=False)
    monkeypatch.setattr(probes.requests, "get", lambda *_a, **_kw: 1 / 0)
    result = probes.probe_photon()
    assert result == {"state": "not_configured", "url_configured": False}


def test_p0_probes_discard_raw_http_payloads(monkeypatch):
    monkeypatch.setenv("PHOTON_BASE_URL", "http://example.test:2322")
    calls = []

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        response = Mock()
        response.status_code = 200
        response.raise_for_status.return_value = None
        response.json.return_value = (
            {"features": [{"properties": {"name": "PRIVATE SAMPLE"}, "geometry": {"coordinates": [1, 2]}}]}
            if "2322" in url
            else {"data": {"features": [
                {"properties": {"cad_num": "47:22:0325001:373"}, "geometry": {"type": "Point"}},
            ]}}
        )
        return response

    monkeypatch.setattr(probes.requests, "get", fake_get)
    photon = probes.probe_photon()
    nspd = probes.probe_nspd()
    assert photon["state"] == "reachable"
    assert photon["feature_count"] == 1
    assert nspd["exact_cadastral_labels"] == 1
    assert nspd["features_with_any_geometry"] == 1
    serialized = json.dumps((photon, nspd))
    assert "PRIVATE SAMPLE" not in serialized
    assert len(calls) == 2
    assert calls[1][1]["verify"] is not False
