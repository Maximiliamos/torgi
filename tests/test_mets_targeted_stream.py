from __future__ import annotations

import pytest

from bankrotai.connectors.registry.mets_public import (
    MetsEvidenceError, _read_bounded_detail_html, parse_mets_lot,
)


SKU = "244025-МЭТС-5"
URL = "https://m-ets.ru/244025-5"


def view(sku=SKU):
    return (
        '<div class="generalview-container"><div class="lot-container">'
        f'<meta itemprop="sku" content="{sku}">'
        '<h2>Земельный участок в Ярославле</h2>'
        '<div class="lot-cost-item"><div class="value">1 234 567 ₽</div>'
        '<div class="title">Начальная цена</div></div>'
        '<div class="lot-cost-item status"><div class="lot-status-name">'
        'Торги в стадии приема заявок</div></div></div>'
        '<div class="lot-info-block info-type_1"><div class="kad_num">'
        '<span class="kn_span">76:23:0000001:123</span></div></div></div>'
    )


class Response:
    headers = {"Content-Type": "text/html; charset=utf-8"}
    encoding = "utf-8"

    def __init__(self, body):
        self.body = body.encode("utf-8")
        self.consumed = 0
        self.closed = False

    def iter_content(self, chunk_size):
        for offset in range(0, len(self.body), 113):
            part = self.body[offset:offset + 113]
            self.consumed += len(part)
            yield part

    def close(self):
        self.closed = True


def test_late_exact_view_matches_full_document_and_stops_before_recommendations():
    body = '<html><body>' + view("244025-МЭТС-6") + 'x' * 3000 + view() + 'x' * 20000
    response = Response(body)
    html = _read_bounded_detail_html(response, limit=2000, expected_sku=SKU)
    actual = parse_mets_lot(html, URL)
    expected = parse_mets_lot(body, URL)
    assert (actual.external_id, actual.start_price, actual.cadastral_number, actual.auction_status) == (
        expected.external_id, expected.start_price, expected.cadastral_number, expected.auction_status,
    )
    assert response.closed
    assert response.consumed < 6000
    assert len(html.encode("utf-8")) < 2000
    assert actual.raw_data["detail_html_truncated"]


@pytest.mark.parametrize("body", [
    view()[:-12] + 'x' * 4000,
    '<noindex>' + view() + '</noindex>' + 'x' * 4000,
    view().replace(f'content="{SKU}"', 'content="244025-МЭТС-6"') + 'x' * 4000,
])
def test_incomplete_recommended_or_wrong_view_never_becomes_target(body):
    response = Response(body)
    with pytest.raises(MetsEvidenceError):
        html = _read_bounded_detail_html(response, limit=2000, expected_sku=SKU)
        parse_mets_lot(html, URL)
    assert response.closed


def test_scan_budget_closes_response_without_unbounded_buffer(monkeypatch):
    import bankrotai.connectors.registry.mets_public as mod

    monkeypatch.setattr(mod, "MAX_DETAIL_SCAN_BYTES", 2500)
    response = Response('x' * 5000 + view())
    with pytest.raises(MetsEvidenceError, match="safety limit"):
        _read_bounded_detail_html(response, limit=2000, expected_sku=SKU)
    assert response.closed
    assert response.consumed <= 2613


@pytest.mark.parametrize("phrase,status", [
    ("Торги завершены", "closed"), ("Торги не состоялись", "closed"),
    ("Торги приостановлены", "paused"), ("Прием заявок завершен", "unknown"),
])
def test_exact_lot_terminal_status_is_explicit_not_inferred(phrase, status):
    html = view().replace("Торги в стадии приема заявок", phrase)
    html = html.replace("Земельный участок в Ярославле", "Земельный участок в Ярославле, площадью 1000 кв.м., для жилищного строительства")
    assert parse_mets_lot(html, URL).auction_status == status
