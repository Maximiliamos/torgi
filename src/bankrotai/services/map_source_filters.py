"""Map source catalogue and canonical source-match contract (read-only).

Pilot ETPs are displayed for planning, not marked as published or complete.
A merged source lot remains visible for each source in its duplicate group.
"""
from __future__ import annotations

from sqlalchemy import exists, or_, select
from sqlalchemy.orm import aliased

from bankrotai.db import ProcessedLot

SOURCE_LABELS = {
    "torgi.gov.ru": "ГИС Торги",
    "lot-online.ru": "Lot-online / РАД",
    "tbankrot.ru": "Т-Банкрот",
    "bidexpert.ru": "BidExpert",
    "torgi-russia.ru": "Торги России",
    "fedresurs.ru": "ЕФРСБ",
    "rts-tender.ru": "РТС-тендер",
    "m-ets.ru": "МЭТС",
    "cdtrf.ru": "ЦДТ",
    "alfalot.ru": "Альфалот",
    "nistp.ru": "НИС",
    "sberbank-ast.ru": "Сбербанк-АСТ",
    "fabrikant.ru": "Фабрикант",
    "arbbitlot.ru": "АРБбитЛот",
    "etpu.ru": "Уральская ЭТП",
    "sistematorg.com": "Систематорг",
    "centerr.ru": "Центр реализации",
    "vertrades.ru": "Вердиктъ",
    "aukcioncenter.ru": "Аукцион-центр",
    "etp-profit.ru": "Профит",
    "ausib.ru": "Аукционы Сибири",
    "utender.ru": "uTender",
    "rus-on.ru": "RUSSIA OnLine",
    "electro-torgi.ru": "Электро-Торги",
    "zakazrf.ru": "ЗаказРФ",
    "roseltorg.ru": "Росэлторг",
    "utpl.ru": "Объединённая торговая площадка",
}
CONNECTED = frozenset({
    "torgi.gov.ru", "lot-online.ru", "tbankrot.ru", "bidexpert.ru", "torgi-russia.ru",
})


def parse_source_selection(raw: str | None) -> tuple[str, ...]:
    """A strict, bounded set for stable cache keys and SQL binds."""
    if raw is None or raw == "":
        return ()
    if len(raw) > 900:
        raise ValueError("Source filter exceeds 900 characters")
    ids = raw.split(",")
    if len(ids) > len(SOURCE_LABELS) or any(not entry or entry != entry.strip() for entry in ids):
        raise ValueError("Invalid source selection")
    unknown = set(ids).difference(SOURCE_LABELS)
    if unknown:
        raise ValueError("Unknown map sources: " + ", ".join(sorted(unknown)))
    return tuple(sorted(set(ids)))


def source_match_condition(sources: tuple[str, ...]):
    """Match primary record OR a merged duplicate's actual source system."""
    if not sources:
        raise ValueError("No sources selected")
    member = aliased(ProcessedLot)
    duplicate_from_source = exists(
        select(1).where(
            member.duplicate_of_id == ProcessedLot.id,
            member.is_archived.is_(False),
            member.source_system.in_(sources),
        )
    )
    return or_(ProcessedLot.source_system.in_(sources), duplicate_from_source)


def map_source_catalogue(session) -> dict:
    present = set(session.scalars(
        select(ProcessedLot.source_system).distinct()
        .where(ProcessedLot.is_archived.is_(False))
    ))
    return {
        "items": [
            {
                "id": key,
                "label": name,
                # CDTRF becomes connected only after actual DB records exist.
                # Deployment or a successful catalogue preview alone is not enough.
                "ingestion_stage": (
                    "connected"
                    if key in CONNECTED or (key == "cdtrf.ru" and key in present)
                    else "pilot"
                ),
                "has_database_records": key in present,
            }
            for key, name in SOURCE_LABELS.items()
        ],
        "filter_semantics": "any_selected_source_in_canonical_duplicate_group",
        "pilot_sources_are_not_automatically_ingested": True,
    }
