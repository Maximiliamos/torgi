"""Strict CDTRF trade/catalog timeline contracts; all data synthetic."""
from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest

from bankrotai.connectors.registry.cdtrf_public import parse_catalogue
from bankrotai.services.etp_cdtrf_canary import (
    CdtrfCanaryError,
    _official_moscow_time,
    fetch_verified_cdtrf_lot,
)


def catalogue(*, time="21.12.2026 10:00:00"):
    return f"""
    <div class="catalog-card">
      <div class="catalog-card__code">Код торгов: 390964</div>
      <div class="catalog-card__status">Статус: Торги объявлены</div>
      <div class="catalog-card__type">Публичное предложение</div>
      <a class="catalog-card__name" href="https://torgi.cdtrf.ru/trades/390964">Земельный участок</a>
      <div class="catalog-card__price-val">901 800 ₽</div>
      <div class="catalog-card__trade-info">
        <div class="catalog-card__trade-info-item">Дата приема заявок: 01.11.2026 09:00:00 - 20.12.2026 18:00:00</div>
        <div class="catalog-card__trade-info-item">Дата проведения торгов: {time}</div>
      </div>
    </div>
    """


def test_real_page_shape_preserves_labelled_auction_time():
    item = parse_catalogue(catalogue())["page_observations"][0]
    assert item["auction_at"] == "2026-12-21T10:00:00"
    assert item["auction_timezone"] == "Europe/Moscow"
    assert item["application_start"] == "01.11.2026 09:00:00"
    assert item["application_end"] == "20.12.2026 18:00:00"
    assert item["trade_lot_id"] is None
    assert item["ready_for_ingestion"] is False


def test_invalid_date_does_not_invent_auction_time():
    item = parse_catalogue(catalogue(time="31.02.2026 10:00:00"))["page_observations"][0]
    assert item["auction_at"] is None
    assert item["auction_timezone"] is None


def test_moscow_datetime_strict_and_tz_aware():
    dt = _official_moscow_time("20.12.2026 18:00")
    assert dt == datetime.fromisoformat("2026-12-20T18:00:00+03:00")
    assert _official_moscow_time(None) is None
    with pytest.raises(CdtrfCanaryError, match="INVALID_TIME"):
        _official_moscow_time("31.02.2026 18:00")


def test_detail_normalizes_application_time_and_schedule(monkeypatch):
    import bankrotai.services.etp_cdtrf_canary as module
    monkeypatch.setattr(module, "_http_get", lambda *a, **k: SimpleNamespace(
        headers={"Content-Type": "text/html"}, text="<html></html>"
    ))
    monkeypatch.setattr(module, "parse_detail", lambda *a, **k: {
        "trade_id": "390964",
        "trade_lot_id": "390998",
        "title": "Земельный участок",
        "property_description": "Земельный участок 56:21:0000001:100",
        "category_label": "Земельные участки",
        "cadastral_number": "56:21:0000001:100",
        "initial_price_rub": "901800.00",
        "source_status_code": "RecieveReq",
        "application_start": "01.11.2026 09:00",
        "application_end": "20.12.2026 18:00",
        "trade_type": "Публичное предложение",
        "efrsb_message_id": "22222",
        "price_periods": [{
            "start": "01.11.2026 09:00:00",
            "end": "09.11.2026 09:00:00",
            "price_rub": "901800.00",
        }],
    })
    lot = fetch_verified_cdtrf_lot("390964", "390998", session=object())
    assert lot.application_start_at.isoformat() == "2026-11-01T09:00:00+03:00"
    assert lot.application_deadline.isoformat() == "2026-12-20T18:00:00+03:00"
    assert lot.auction_at is None  # winnerTime is not the auction start
    assert lot.public_offer_schedule[0]["price_rub"] == "901800.00"
    assert lot.efresb_message_number == "22222"
    assert lot.auction_type == "Публичное предложение"
    assert lot.current_price is None  # do not manufacture current price
