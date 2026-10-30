"""Source selection is strict, stable, read-only and matches any duplicate publication."""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from bankrotai.db import Base, ProcessedLot
from bankrotai.services.map_source_filters import (
    SOURCE_LABELS, map_source_catalogue, parse_source_selection, source_match_condition,
)


def test_existing_and_pilot_source_choices_have_distinct_names():
    assert SOURCE_LABELS["lot-online.ru"] == "Lot-online / РАД"
    assert SOURCE_LABELS["rts-tender.ru"] == "РТС-тендер"
    assert len(SOURCE_LABELS) == 27


def test_validated_sorted_any_of_selection_and_invalid_input():
    assert parse_source_selection(None) == ()
    assert parse_source_selection("") == ()
    assert parse_source_selection("rts-tender.ru,lot-online.ru,rts-tender.ru") == (
        "lot-online.ru", "rts-tender.ru",
    )
    for raw in ("rts-tender.ru,", "evil.example", "lot-online.ru, bad", "a" * 901):
        with pytest.raises(ValueError):
            parse_source_selection(raw)


def test_canonical_lot_visible_for_any_actual_source_of_merged_publication():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(engine)
    with session_factory.begin() as session:
        parent = ProcessedLot(
            source_system="lot-online.ru", source="lot-online.ru",
            external_id="lot-1", title="Здание", description="",
            category="real_estate", auction_status="active",
            is_archived=False,
        )
        other = ProcessedLot(
            source_system="tbankrot.ru", source="tbankrot.ru",
            external_id="lot-2", title="Иной объект", description="",
            category="real_estate", auction_status="active",
            is_archived=False,
        )
        session.add_all([parent, other])
        session.flush()
        rts = ProcessedLot(
            source_system="rts-tender.ru", source="rts-tender.ru",
            external_id="uuid-1", title="Здание", description="",
            category="real_estate", auction_status="active",
            duplicate_of_id=parent.id, is_archived=False,
        )
        session.add(rts)
        session.flush()
        primary, other_id = parent.id, other.id
    from sqlalchemy import select
    with session_factory() as session:
        def ids(sources):
            return list(session.scalars(
                select(ProcessedLot.id)
                .where(ProcessedLot.duplicate_of_id.is_(None))
                .where(source_match_condition(sources))
            ))
        assert ids(("lot-online.ru",)) == [primary]
        assert ids(("rts-tender.ru",)) == [primary]
        assert ids(("rts-tender.ru", "lot-online.ru")) == [primary]
        assert ids(("tbankrot.ru",)) == [other_id]
        catalogue = map_source_catalogue(session)
        options = {row["id"]: row for row in catalogue["items"]}
        assert options["rts-tender.ru"]["has_database_records"] is True
        assert options["m-ets.ru"]["has_database_records"] is False
        assert options["rts-tender.ru"]["ingestion_stage"] == "pilot"
        # A merged or locally present pilot is not automatically accepted.
        assert options["cdtrf.ru"]["ingestion_stage"] == "pilot"
    with session_factory.begin() as session:
        session.add(ProcessedLot(
            source_system="cdtrf.ru", source="cdtrf.ru",
            external_id="cdtrf:900001", title="Земельный участок",
            description="Кадастровый объект", category="land",
            auction_status="active", is_archived=False,
        ))
    with session_factory() as session:
        options = {r["id"]: r for r in map_source_catalogue(session)["items"]}
        assert options["cdtrf.ru"]["has_database_records"] is True
        assert options["cdtrf.ru"]["ingestion_stage"] == "connected"
        assert options["m-ets.ru"]["ingestion_stage"] == "pilot"
    engine.dispose()
