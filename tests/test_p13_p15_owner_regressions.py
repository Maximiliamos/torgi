"""Regression cases from owner-reported public map defects (P13/P14/P15)."""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from bankrotai.db import Base, ProcessedLot
from bankrotai.geo import CadastralGeocoder
from bankrotai.services.public_map_policy import public_map_predicates
from bankrotai.torgi_russia import TorgiRussiaClient, public_auction_status


@pytest.mark.parametrize(("title", "category", "status", "vin"), [
    ("Право заключения договора аренды нежилого помещения 76:23:060521:48", "real_estate", "active", None),
    ("Аренда земельного участка 76:22:010717:536", "land", "active", None),
    ("Земельный участок 76:09:082601:3891", "land", "closed", None),
    ("Мопед Альфа Jaguar RS SPORT", "real_estate", "active", "LWJPCBL24RB000657"),
    ("Автомобиль", "vehicle", "active", None),
])
def test_bad_lots_never_qualify_for_public_map(title: str, category: str, status: str, vin: str | None) -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        row = ProcessedLot(
            source="test", source_system="test", external_id="owner-regression",
            title=title, description=title, category=category,
            auction_status=status, vin=vin, is_archived=False,
            current_price=Decimal("1000"), current_geo_lat=57.6, current_geo_lon=39.8,
        )
        session.add(row)
        session.flush()
        assert session.scalars(select(ProcessedLot).where(*public_map_predicates())).all() == []


def test_confirmed_sale_qualifies_and_suspect_cadastral_geo_does_not() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        lot = ProcessedLot(
            source="test", source_system="test", external_id="76:02:022201:38",
            title="Земельный участок 76:02:022201:38", description="Продажа",
            category="land", auction_status="active",
            cadastral_number="76:02:022201:38", needs_geo_check=True,
            current_geo_lat=57.6, current_geo_lon=39.8,
        )
        session.add(lot)
        session.flush()
        assert session.scalar(select(ProcessedLot.id).where(*public_map_predicates())) is None
        lot.needs_geo_check = False
        assert session.scalar(select(ProcessedLot.id).where(*public_map_predicates())) == lot.id


def test_nspd_search_refuses_unrelated_first_feature() -> None:
    geocoder = CadastralGeocoder()
    requested = "76:02:022201:38"
    other = {"properties": {"options": {"cad_num": "76:02:022201:39"}}}
    matching = {"properties": {"options": {"cad_num": requested}}}
    assert geocoder._pick_nspd_feature([other], requested) is None
    assert geocoder._pick_nspd_feature([other, matching], requested) == matching


def test_pkk_search_refuses_unrelated_first_feature() -> None:
    geocoder = CadastralGeocoder()
    assert geocoder._parse_pkk_feature(
        {"features": [{"attrs": {"cn": "76:02:022201:39"}, "center": {"x": 39.5, "y": 57.5}}]},
        "76:02:022201:38", "land_plot",
    ) is None


@pytest.mark.parametrize(("upstream", "expected"), [
    ("Торги завершены", "closed"),
    ("Не состоялись", "closed"),
    ("Отменены", "closed"),
    ("Идёт приём заявок", "active"),
    ("Новый статус, которого не знаем", "unknown"),
    (None, "unknown"),
])
def test_torgi_russia_public_status_mapping(upstream: object, expected: str) -> None:
    assert public_auction_status(upstream) == expected


def test_torgi_russia_closed_status_is_not_promoted_to_active() -> None:
    payload = {"data": [{
        "id": 123, "title": "Земельный участок 76:09:082601:3891",
        "status": "Торги завершены", "region_title": "Ярославская область",
        "start_price": 1000, "current_price": 900,
    }]}
    lots = TorgiRussiaClient.parse_search_payload(payload)
    assert len(lots) == 1
    assert lots[0].auction_status == "closed"
