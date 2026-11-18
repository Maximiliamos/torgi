from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from bankrotai.db import AuditLog, Base
from bankrotai.domain import NormalizedLot
from bankrotai.services.source_lot_exclusions import (
    excluded_source_identities,
    filter_excluded_lots,
)


def _lot(source_system: str, external_id: str) -> NormalizedLot:
    return NormalizedLot(
        external_id=external_id,
        source=source_system,
        source_system=source_system,
        title="Земельный участок",
        description="Продажа участка",
        category="land",
        region_slug="76",
        region_name="Ярославская область",
        address="Ярославль",
        cadastral_number="76:23:0000001:1",
        vin=None,
        area=None,
        start_price=100,
        current_price=100,
        auction_status="active",
        lot_url="https://example.test/lot",
        source_url="https://example.test/lot",
        detail_level="detail",
        raw_data={},
    )


def _session() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def test_removed_exact_identity_is_excluded_but_sibling_is_kept() -> None:
    session = _session()
    session.add(
        AuditLog(
            user_id="operator",
            action="remove_reviewed_reported_lot",
            entity_type="lot",
            entity_id="123",
            metadata_json={
                "source_system": "tbankrot.ru",
                "external_id": "7903020",
                "reason": "explicit_user_request_remove_incorrect_reviewed_lots",
            },
        )
    )
    session.commit()

    removed = _lot("tbankrot.ru", "7903020")
    sibling = _lot("tbankrot.ru", "7903021")

    assert excluded_source_identities(session) == {("tbankrot.ru", "7903020")}
    kept, count = filter_excluded_lots(session, [removed, sibling])
    assert count == 1
    assert [lot.external_id for lot in kept] == ["7903021"]


def test_unrelated_audit_action_does_not_exclude_source_identity() -> None:
    session = _session()
    session.add(
        AuditLog(
            user_id="operator",
            action="exact_report_source_repair",
            entity_type="lot",
            entity_id="123",
            metadata_json={"source_system": "tbankrot.ru", "external_id": "7903020"},
        )
    )
    session.commit()

    lot = _lot("tbankrot.ru", "7903020")
    kept, count = filter_excluded_lots(session, [lot])
    assert count == 0
    assert kept == [lot]


def test_malformed_historical_audit_metadata_is_ignored() -> None:
    session = _session()
    session.add_all([
        AuditLog(
            user_id="operator",
            action="remove_reviewed_reported_lot",
            entity_type="lot",
            entity_id="1",
            metadata_json=None,
        ),
        AuditLog(
            user_id="operator",
            action="remove_reviewed_reported_lot",
            entity_type="lot",
            entity_id="2",
            metadata_json={"source_system": "tbankrot.ru"},
        ),
    ])
    session.commit()

    assert excluded_source_identities(session) == set()
