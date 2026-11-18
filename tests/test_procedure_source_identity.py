from contextlib import contextmanager
from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from bankrotai.db import Base, CanonicalLot, ProcessedLot, SourceLot


@pytest.mark.parametrize("source_system", ["bidexpert.ru", "lot-online.ru"])
def test_procedure_uses_exact_identity_and_keeps_unverified_dates_historical(monkeypatch, source_system):
    from bankrotai import api
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        lot = ProcessedLot(source_system=source_system, external_id="own", title="Участок", category="land")
        canonical = CanonicalLot(canonical_key="procedure-identity", title="Участок", category="land")
        session.add_all([lot, canonical])
        session.flush()
        wrong = SourceLot(canonical_lot_id=canonical.id, processed_lot_id=lot.id,
                          source_system="other.ru", external_id="different-auction", procedure_number="wrong",
                          application_deadline=datetime(2030, 1, 1))
        own = SourceLot(canonical_lot_id=canonical.id, processed_lot_id=None,
                        source_system=source_system, external_id="own", procedure_number="correct",
                        application_deadline=datetime(2026, 12, 1, 18),
                        raw_data={"detail_enrichment_version": 3})
        session.add_all([wrong, own])
        session.commit()

        @contextmanager
        def read_scope():
            yield session

        monkeypatch.setattr(api, "read_session_scope", read_scope)
        result = api.get_lot_procedure(lot.id)
        assert result["source_lot_id"] == own.id
        assert result["procedure_number"] == "correct"
        if source_system == "lot-online.ru":
            assert result["application_deadline"] is None
            assert result["unverified_auction_fields"] == ["application_deadline"]
        else:
            assert result["application_deadline"] == own.application_deadline
            assert result["unverified_auction_fields"] == []
        assert own.application_deadline == datetime(2026, 12, 1, 18)
    engine.dispose()
