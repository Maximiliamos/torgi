from __future__ import annotations

import platform
from dataclasses import asdict
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from bankrotai import __version__
from bankrotai.core import utc_now
from bankrotai.db import (
    DiagnosticEvent,
    LotNote,
    LotParticipationChecklist,
    ProcessedLot,
    SavedMaxBidScenario,
    SavedSearch,
    SourceLot,
    Watchlist,
)
from bankrotai.finance import MaxBidInputs, calculate_max_bid
from bankrotai.services.quality import data_quality_snapshot, list_source_health


def save_max_bid_scenario(
    session: Session,
    lot_id: int,
    inputs: MaxBidInputs,
    *,
    name: str,
    user_id: str = "desktop",
) -> SavedMaxBidScenario:
    if session.get(ProcessedLot, lot_id) is None:
        raise ValueError(f"Lot {lot_id} does not exist")
    results = calculate_max_bid(inputs)
    scenario = SavedMaxBidScenario(
        lot_id=lot_id,
        user_id=user_id,
        name=name.strip() or f"Расчёт {utc_now():%d.%m.%Y %H:%M}",
        inputs_json=asdict(inputs),
        results_json={key: asdict(value) for key, value in results.items()},
    )
    session.add(scenario)
    session.flush()
    return scenario


def save_participation_checklist(
    session: Session,
    lot_id: int,
    values: dict[str, Any],
    *,
    user_id: str = "desktop",
) -> LotParticipationChecklist:
    source_lot = session.scalar(select(SourceLot).where(SourceLot.processed_lot_id == lot_id))
    if source_lot is None:
        raise ValueError("Source lot does not exist")
    checklist = session.scalar(select(LotParticipationChecklist).where(
        LotParticipationChecklist.source_lot_id == source_lot.id,
        LotParticipationChecklist.user_id == user_id,
    ))
    if checklist is None:
        checklist = LotParticipationChecklist(source_lot_id=source_lot.id, user_id=user_id)
        session.add(checklist)
    allowed = {
        "etp_accredited", "signature_valid", "application_completed", "deposit_sent",
        "payment_purpose_verified", "deposit_received", "documents_signed",
        "application_accepted", "notes",
    }
    for key, value in values.items():
        if key in allowed:
            setattr(checklist, key, value)
    checklist.updated_at = utc_now()
    session.flush()
    return checklist


def toggle_watchlist(session: Session, lot_id: int, *, user_id: str = "desktop") -> bool:
    if session.get(ProcessedLot, lot_id) is None:
        raise ValueError(f"Lot {lot_id} does not exist")
    entry = session.scalar(select(Watchlist).where(Watchlist.user_id == user_id, Watchlist.lot_id == lot_id))
    if entry is not None:
        session.delete(entry)
        session.flush()
        return False
    session.add(Watchlist(user_id=user_id, lot_id=lot_id))
    session.flush()
    return True


def add_lot_note(session: Session, lot_id: int, content: str, *, user_id: str = "desktop") -> LotNote:
    text = content.strip()
    if not text:
        raise ValueError("Note cannot be empty")
    if session.get(ProcessedLot, lot_id) is None:
        raise ValueError(f"Lot {lot_id} does not exist")
    note = LotNote(lot_id=lot_id, user_id=user_id, content=text)
    session.add(note)
    session.flush()
    return note


def save_search(
    session: Session,
    name: str,
    query_params: dict[str, Any],
    *,
    user_id: str = "desktop",
) -> SavedSearch:
    search = SavedSearch(
        user_id=user_id,
        name=name.strip() or f"Поиск {utc_now():%d.%m.%Y %H:%M}",
        query_params=query_params,
    )
    session.add(search)
    session.flush()
    return search


def diagnostic_export(session: Session) -> dict[str, Any]:
    quality = data_quality_snapshot(session)
    sources = list_source_health(session)
    recent = session.scalars(select(DiagnosticEvent).order_by(desc(DiagnosticEvent.created_at)).limit(100)).all()
    return {
        "generated_at": utc_now().isoformat(),
        "application_version": __version__,
        "runtime": {"python": platform.python_version(), "platform": platform.platform()},
        "quality": quality.model_dump(mode="json"),
        "sources": [item.model_dump(mode="json") for item in sources],
        "events": [
            {
                "severity": item.severity,
                "component": item.component,
                "message": item.message,
                "context": item.context_json,
                "created_at": item.created_at.isoformat() if item.created_at else None,
            }
            for item in recent
        ],
    }
