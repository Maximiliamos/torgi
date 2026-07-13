from __future__ import annotations

from datetime import date, timedelta

from bankrotai.scrapers import GorodTorgiClient, PublicRealEstateLot, _parse_public_date, _public_lot_from_payload


def test_extract_tbankrot_status_from_container_text() -> None:
    text = "Лот завершен, торги состоявшийся, дата публикации 15 апреля 2026"
    status = GorodTorgiClient._extract_tbankrot_status(text)
    assert status in {"Состоявшийся", "Завершено"}


def test_resolve_tbankrot_reference_derives_active_status_from_future_trade_date() -> None:
    future_date = (date.today() + timedelta(days=10)).strftime("%d %B %Y")
    client = GorodTorgiClient(city_slug="yaroslavl", resolve_tbankrot=False)
    lot = PublicRealEstateLot(
        source="gorod-torgi.ru",
        category="real-estate",
        published_at="16 April, 2026",
        asset_type="real_estate",
        status="Опубликовано",
        price="1000000",
        title="Lot",
        location="Ярославль, ул. Калмыковых, 4",
        url="https://example.com/1",
        reference_url="https://example.com/1",
        source_label="source",
    )

    resolved = client._resolve_tbankrot_reference_from_text(
        lot,
        container_text=f"Конкурс в электронной форме, прием заявок до {future_date}",
        item_url="https://tbankrot.ru/item?id=1",
    )

    assert resolved.tbankrot_status == "Приём заявок"
    assert resolved.tbankrot_status_note.startswith(("Derived from TBankrot trade date:", "TBankrot matched item:"))


def test_public_lot_from_payload_restores_secondary_status_fields() -> None:
    lot = _public_lot_from_payload(
        {
            "source": "gorod-torgi.ru",
            "categorySlug": "real-estate",
            "published_at_text": "16 April, 2026",
            "asset_type": "real_estate",
            "status": "Опубликовано",
            "price_text": "1000000",
            "title": "Lot",
            "location": "Yaroslavl",
            "url": "https://example.com/1",
            "reference_url": "https://tbankrot.ru/item?id=1",
            "sourceLabel": "TBankrot item",
            "tbankrot_status": "Завершено",
            "tbankrot_status_note": "authoritative",
            "tbankrot_status_checked_at": "2026-04-16T10:00:00Z",
        }
    )

    assert lot.tbankrot_status == "Завершено"
    assert lot.tbankrot_status_note == "authoritative"
    assert lot.tbankrot_status_checked_at == "2026-04-16T10:00:00Z"


def test_parse_public_date_handles_russian_month_with_comma() -> None:
    parsed = _parse_public_date("15 \u0430\u043f\u0440\u0435\u043b\u044f, 2026")

    assert parsed is not None
    assert parsed.date().isoformat() == "2026-04-15"
