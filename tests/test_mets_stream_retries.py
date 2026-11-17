import pytest
import requests

from bankrotai.connectors.registry.mets_public import MetsPublicPilot, parse_mets_lot


URL = "https://m-ets.ru/244025-5"
VIEW = '''<div class="lot-container"><meta itemprop="sku" content="244025-МЭТС-5">
<h2>Земельный участок, площадью 1000 кв.м., для жилищного строительства в Ярославле</h2>
<span itemprop="description">Кадастровый номер: 76:23:0000001:123</span>
<div class="lot-cost-item"><div class="value">1 000 000 ₽</div><div class="title">Начальная цена</div></div>
<div class="lot-cost-item status"><div class="lot-status-name">Объявленные торги</div></div></div>'''


class Response:
    status_code = 200
    encoding = "utf-8"
    headers = {"Content-Type": "text/html; charset=utf-8"}

    def __init__(self, body=VIEW, fail=False):
        self.body = body
        self.fail = fail
        self.closed = False

    def raise_for_status(self):
        pass

    def iter_content(self, chunk_size):
        if self.fail:
            yield b"<html>interrupted stream"
            raise requests.exceptions.ChunkedEncodingError("Response ended prematurely")
        yield self.body.encode("utf-8")

    def close(self):
        self.closed = True


class Session(requests.Session):
    def __init__(self, responses):
        super().__init__()
        self.responses = responses
        self.calls = []

    def get(self, url, **kwargs):
        assert kwargs["stream"]
        self.calls.append(url)
        return self.responses[len(self.calls) - 1]


def test_midstream_disconnect_is_retried_without_accepting_partial_data(monkeypatch):
    monkeypatch.setattr("bankrotai.connectors.registry.mets_public.time.sleep", lambda _: None)
    responses = [Response(fail=True), Response()]
    session = Session(responses)
    html = MetsPublicPilot(session=session)._get(URL)
    assert parse_mets_lot(html, URL).start_price == 1_000_000
    assert len(session.calls) == 2
    assert session.calls[0] == session.calls[1]
    assert all(response.closed for response in responses)


def test_persistent_midstream_failure_has_a_fixed_retry_budget(monkeypatch):
    monkeypatch.setattr("bankrotai.connectors.registry.mets_public.time.sleep", lambda _: None)
    responses = [Response(fail=True) for _ in range(4)]
    session = Session(responses)
    with pytest.raises(requests.exceptions.ChunkedEncodingError):
        MetsPublicPilot(session=session)._get(URL)
    assert len(session.calls) == 4
    assert all(response.closed for response in responses)


def test_observed_spinner_is_temporarily_skipped_for_next_exact_lot(monkeypatch):
    monkeypatch.setattr("bankrotai.connectors.registry.mets_public.time.sleep", lambda _: None)
    placeholder = '<div class="gorizontal-vertikal"><img src="data:image/gif;base64,spinner"></div>'
    session = Session([Response(placeholder), Response(), Response(VIEW.replace("МЭТС-5", "МЭТС-6"))])
    pilot = MetsPublicPilot(session=session)
    pilot._get(URL)
    pilot._get("https://m-ets.ru/244025-6")
    assert session.calls == ["https://meetings.m-ets.ru/244025-5", URL, "https://m-ets.ru/244025-6"]
