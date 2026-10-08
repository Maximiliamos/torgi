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
        lot.current_geo_source = "nspd"
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


def test_legacy_tbankrot_requires_recent_independent_canonical_projection() -> None:
    from datetime import datetime, timezone
    from bankrotai.db import CanonicalLot, SourceLot

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        lot = ProcessedLot(
            source="tbankrot", source_system="tbankrot.ru", external_id="old-source",
            title="Земельный участок", description="", category="land",
            auction_status="active", current_geo_lat=57.6, current_geo_lon=39.8,
        )
        session.add(lot)
        session.flush()
        canonical = CanonicalLot(
            canonical_key="owner-p14-tbankrot", legacy_processed_lot_id=lot.id,
            title=lot.title, category="land",
        )
        session.add(canonical)
        session.flush()
        assert session.scalar(select(ProcessedLot.id).where(*public_map_predicates())) is None
        session.add(SourceLot(
            canonical_lot_id=canonical.id, source_system="torgi.gov.ru",
            external_id="independent-current",
            is_active=True, is_archived=False, source_status="active",
            last_seen_at=datetime.now(timezone.utc).replace(tzinfo=None),
        ))
        session.flush()
        assert session.scalar(select(ProcessedLot.id).where(*public_map_predicates())) == lot.id

        active_source = session.scalars(select(SourceLot).where(SourceLot.canonical_lot_id == canonical.id)).one()
        active_source.source_status = "closed"
        session.flush()
        assert session.scalar(select(ProcessedLot.id).where(*public_map_predicates())) is None


def test_rental_transaction_in_source_body_excluded_even_with_generic_land_title() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        lot = ProcessedLot(
            source="test", source_system="test", external_id="76:22:010717:536",
            title="Земельный участок 6.79 сотки",
            description="Вид торгов : Аренда. Кадастровый номер 76:22:010717:536",
            category="land", auction_status="active",
            current_geo_lat=57.5, current_geo_lon=39.5,
        )
        session.add(lot)
        session.flush()
        assert session.scalar(select(ProcessedLot.id).where(*public_map_predicates())) is None


def test_cadastral_address_fallback_is_never_final_even_when_photon_says_high() -> None:
    from bankrotai.geo import CadastralObjectResult, apply_lot_geo_result

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        lot = ProcessedLot(
            source="test", source_system="test", external_id="76:02:022201:38",
            title="Земельный участок 76:02:022201:38",
            description="", category="land", auction_status="active",
            cadastral_number="76:02:022201:38",
        )
        session.add(lot)
        session.flush()
        weak = CadastralObjectResult(
            query="Борисоглебский район", cadastral_number=None,
            lat=57.7, lon=39.2, source="photon", confidence="high",
        )
        assert apply_lot_geo_result(session, lot, weak) is True
        assert lot.needs_geo_check is True
        assert session.scalar(select(ProcessedLot.id).where(*public_map_predicates())) is None
        trusted = CadastralObjectResult(
            query=lot.cadastral_number, cadastral_number=lot.cadastral_number,
            lat=57.6, lon=39.8, source="nspd", confidence="high",
        )
        assert apply_lot_geo_result(session, lot, trusted) is True
        assert lot.needs_geo_check is False


def test_cadastral_provider_result_with_wrong_id_still_requires_review() -> None:
    from bankrotai.services.cadastral_identity import geo_result_needs_review
    assert geo_result_needs_review(
        "76:02:022201:38", None, "76:02:022201:39", "nspd", "high"
    ) is True
    assert geo_result_needs_review(
        "76:02:022201:38", None, "76:02:022201:38", "nspd", "high"
    ) is False
