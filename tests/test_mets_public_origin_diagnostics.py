"""Read-only public-origin diagnostic never forwards HTML, PII or credentials."""
from __future__ import annotations

import json

import pytest
import requests

from scripts.diagnose_mets_public_origins import PUBLIC_ID, probe_origin


class Response:
    def __init__(self, status=200, text="", location=None):
        self.status_code = status
        self.text = text
        self.content = text.encode()
        self.headers = {"Content-Type": "text/html"}
        if location is not None:
            self.headers["Location"] = location


class Session:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    def get(self, url, *, timeout, allow_redirects, headers):
        assert allow_redirects is False
        assert timeout == (5, 12)
        assert "Authorization" not in headers
        self.calls.append(url)
        if self.error is not None:
            raise self.error
        return self.response


def test_diagnostic_accepts_only_two_official_hosts_and_known_public_id():
    with pytest.raises(ValueError, match="unapproved"):
        probe_origin("evil.example", session=Session())
    with pytest.raises(ValueError, match="non-approved"):
        probe_origin("m-ets.ru", lot_id="999-1", session=Session())


@pytest.mark.parametrize("host", ["m-ets.ru", "meetings.m-ets.ru"])
def test_diagnostic_is_single_request_and_fails_closed_on_non_200(host):
    session = Session(Response(403, "PRIVATE CONTACT INFORMATION"))
    report = probe_origin(host, session=session)
    assert report["http_status"] == 403
    assert report["reason"] == "http_non_200"
    assert report["schema_verified"] is False
    assert len(session.calls) == 1
    assert "PRIVATE" not in json.dumps(report)


def test_diagnostic_distinguishes_verified_official_redirect_from_untrusted():
    location = f"https://meetings.m-ets.ru/{PUBLIC_ID}"
    session = Session(Response(302, "", location))
    report = probe_origin("m-ets.ru", session=session)
    assert report["redirect_to_official_same_id"] is True
    assert report["reason"] == "official_identity_preserving_redirect"
    assert session.calls == [f"https://m-ets.ru/{PUBLIC_ID}"]

    for bad in (
        "https://evil.example/244602-1",
        "http://meetings.m-ets.ru/244602-1",
        "https://meetings.m-ets.ru/244602-2",
        "https://meetings.m-ets.ru/244602-1?secret=1",
    ):
        report = probe_origin("m-ets.ru", session=Session(Response(302, "", bad)))
        assert report["redirect_to_official_same_id"] is False
        assert report["reason"] == "untrusted_redirect"


def test_diagnostic_network_failure_has_only_exception_class():
    session = Session(error=requests.Timeout("PRIVATE URL user@example.test"))
    report = probe_origin("meetings.m-ets.ru", session=session)
    assert report["reason"] == "Timeout"
    assert "PRIVATE" not in json.dumps(report)
    assert "example.test" not in json.dumps(report)


def test_diagnostic_never_copies_source_html_on_schema_failure():
    session = Session(Response(200, "PRIVATE debtor details, incomplete lot"))
    report = probe_origin("meetings.m-ets.ru", session=session)
    assert report["http_status"] == 200
    assert report["reason"] == "detail_schema_invalid"
    assert report["schema_verified"] is False
    assert "PRIVATE" not in json.dumps(report)
