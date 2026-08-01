from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from bankrotai.connectors.registry import connector_registry
from bankrotai.connectors.registry.fedresurs import FedresursConnector
from bankrotai.db import Base, CanonicalLot, ProcessedLot, SourceLot
from bankrotai.documents import record_document_version
from bankrotai.domain import NormalizedLot
from bankrotai.finance import MaxBidInputs, calculate_max_bid
from bankrotai.logic import persist_lot


def _engine():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine


def _lot(source: str, external_id: str, **overrides) -> NormalizedLot:
    values = dict(
        external_id=external_id,
        source=source,
        source_system=source,
        title="Auction lot",
        description="Description",
        category="land",
        region_slug="76",
        region_name=None,
        address="Yaroslavl",
        cadastral_number="76:23:010101:10",
        vin=None,
        area=1000.0,
        start_price=1_000_000,
        current_price=900_000,
        auction_status="active",
        lot_url=None,
        source_url="https://example.com/lot",
        detail_level="detail",
        raw_data={},
    )
    values.update(overrides)
    return NormalizedLot(**values)


def test_source_lots_from_two_systems_share_canonical_asset() -> None:
    with Session(_engine()) as session:
        first = persist_lot(session, _lot("efrsb", "message-1"))
        second = persist_lot(session, _lot("etp", "trade-7"))
        session.flush()

        source_lots = session.scalars(select(SourceLot).order_by(SourceLot.id)).all()
        processed_lots = session.scalars(select(ProcessedLot).order_by(ProcessedLot.id)).all()
        assert first.id == second.id
        assert len(processed_lots) == 2
        assert processed_lots[1].duplicate_of_id == processed_lots[0].id
        assert len(source_lots) == 2
        assert source_lots[0].canonical_lot_id == source_lots[1].canonical_lot_id
        assert session.query(CanonicalLot).count() == 1


def test_cross_source_copy_without_cadastral_number_is_hidden_by_address_and_price() -> None:
    with Session(_engine()) as session:
        first = persist_lot(session, _lot(
            "tbankrot.ru",
            "tbankrot:7884064",
            address="Ярославская область, Ивановское, улица Ленина, дом 21",
            current_price=24_356.42,
        ))
        second = persist_lot(session, _lot(
            "lot-online.ru",
            "lot-online:1760257",
            title="Имущество муниципального округа по адресу Ивановское, Ленина, 21",
            address="Ивановское, ул. Ленина, д. 21",
            cadastral_number=None,
            current_price=24_356.42,
        ))
        session.flush()

        copy = session.scalar(select(ProcessedLot).where(
            ProcessedLot.external_id == "lot-online:1760257"
        ))
        assert second.id == first.id
        assert copy is not None
        assert copy.duplicate_of_id == first.id


def test_procedure_fields_are_queryable_and_not_only_raw_json() -> None:
    with Session(_engine()) as session:
        lot = _lot(
            "torgi.gov.ru",
            "lot-1",
            raw_data={
                "etp": "Test ETP",
                "notice_number": "N-42",
                "deposit": 100_000,
                "bidd_end_time": "2030-01-02 12:30:00",
            },
        )
        persist_lot(session, lot)
        source_lot = session.scalar(select(SourceLot))

        assert source_lot is not None
        assert source_lot.platform_name == "Test ETP"
        assert source_lot.notice_number == "N-42"
        assert float(source_lot.deposit_amount) == 100_000
        assert source_lot.application_deadline == datetime(2030, 1, 2, 12, 30)


def test_document_versions_are_content_addressed_and_deduplicated() -> None:
    with Session(_engine()) as session:
        persist_lot(session, _lot("source", "lot-1"))
        source_lot = session.scalar(select(SourceLot))
        assert source_lot is not None

        _, first, first_created = record_document_version(
            session,
            source_lot_id=source_lot.id,
            external_document_id="contract",
            filename="contract.pdf",
            content=b"version one",
            storage_key="documents/contract-v1.pdf",
        )
        _, duplicate, duplicate_created = record_document_version(
            session,
            source_lot_id=source_lot.id,
            external_document_id="contract",
            filename="contract.pdf",
            content=b"version one",
            storage_key="documents/contract-v1-copy.pdf",
        )
        _, second, second_created = record_document_version(
            session,
            source_lot_id=source_lot.id,
            external_document_id="contract",
            filename="contract.pdf",
            content=b"version two",
            storage_key="documents/contract-v2.pdf",
        )

        assert first_created is True
        assert duplicate_created is False
        assert second_created is True
        assert duplicate.id == first.id
        assert second.sha256 != first.sha256


def test_maximum_bid_formula_and_scenarios_are_transparent() -> None:
    scenarios = calculate_max_bid(MaxBidInputs(
        conservative_sale_price=10_000_000,
        repair_cost=500_000,
        legal_cost=100_000,
        monthly_holding_cost=50_000,
        holding_months=6,
        taxes=100_000,
        sale_commission_percent=2,
        target_profit=1_500_000,
        risk_reserve=300_000,
        annual_capital_cost_percent=12,
        intended_bid=6_000_000,
    ))

    assert set(scenarios) == {"pessimistic", "base", "optimistic"}
    assert scenarios["pessimistic"].maximum_bid < scenarios["base"].maximum_bid
    assert scenarios["base"].maximum_bid < scenarios["optimistic"].maximum_bid
    assert scenarios["base"].expected_profit is not None
    assert scenarios["base"].breakeven_sale_price is not None


def test_finance_rejects_impossible_inputs() -> None:
    with pytest.raises(ValueError):
        calculate_max_bid(MaxBidInputs(conservative_sale_price=0))


def test_builtin_connector_registry_is_explicit() -> None:
    assert connector_registry.source_ids() == ("fedresurs.ru", "lot-online.ru", "tbankrot.ru", "torgi.gov.ru")
    assert connector_registry.create("lot-online.ru").source_id == "lot-online.ru"
    assert connector_registry.create("torgi.gov.ru").source_id == "torgi.gov.ru"


def test_fedresurs_connector_authenticates_and_normalizes_official_message() -> None:
    class Response:
        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self.payload

    class SessionStub:
        def __init__(self):
            self.auth_body = None

        def post(self, _url, *, json, timeout):
            self.auth_body = json
            return Response({"jwt": "token"})

        def get(self, _url, *, params, headers, timeout):
            assert headers == {"Authorization": "Bearer token", "Accept": "application/json"}
            return Response({"items": [{"guid": "guid-1", "title": "Land lot", "price": 123}]})

    session = SessionStub()
    connector = FedresursConnector(login="user", password="secret", base_url="https://official.example", session=session)
    page = connector._search_sync({}, None)

    assert session.auth_body["login"] == "user"
    assert len(session.auth_body["passwordHash"]) == 128
    assert page.items[0].external_id == "guid-1"
    assert page.items[0].source_system == "fedresurs.ru"
