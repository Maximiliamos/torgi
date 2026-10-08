"""P13: one conservative public map policy shared by builders and read models.

Only current real-estate sale candidates may be presented as public map points.
The historical registry retains excluded rows for audit and recovery.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import not_, or_, select

from bankrotai.db import CanonicalLot, ProcessedLot, SourceLot
from bankrotai.services.real_estate_filter import REAL_ESTATE_CATEGORIES

TRUSTED_CADASTRAL_GEO_SOURCES = frozenset({"nspd", "ik12_cadastral", "pkk"})
AUTOMATIC_PUBLIC_SOURCES = frozenset({"torgi.gov.ru", "lot-online.ru", "torgi-russia.ru", "bidexpert.ru"})

PUBLIC_ACTIVE_STATUSES = frozenset({
    "active", "scheduled", "published", "open", "applications_submission",
})
# Unknown categories/statuses are ineligible, even when is_archived is stale.
PUBLIC_EXCLUDED_TITLE_TERMS = (
    "аренд", "субаренд", "договор найма", "право пользования",
    "мопед", "мотоцикл", "автомобил", "транспортн", "прицеп",
    "грузовик", "спецтехник", "катер", "лодк", "снегоход",
)


def fresh_independent_source_projection():
    """Correlated EXISTS used both by eligibility and preflight instrumentation."""
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=72)
    return (
        select(SourceLot.id)
        .join(CanonicalLot, SourceLot.canonical_lot_id == CanonicalLot.id)
        .where(
            CanonicalLot.legacy_processed_lot_id == ProcessedLot.id,
            SourceLot.source_system.in_(AUTOMATIC_PUBLIC_SOURCES),
            SourceLot.is_active.is_(True),
            SourceLot.is_archived.is_(False),
            SourceLot.last_seen_at >= cutoff,
        )
        .exists()
    )


def public_map_predicates() -> tuple:
    """Composable SQLAlchemy WHERE clauses: no in-memory post-limit filtering."""
    title = ProcessedLot.title
    fresh_independent_projection = fresh_independent_source_projection()
    return (
        ProcessedLot.duplicate_of_id.is_(None),
        ProcessedLot.is_archived.is_(False),
        ProcessedLot.auction_status.in_(PUBLIC_ACTIVE_STATUSES),
        ProcessedLot.category.in_(REAL_ESTATE_CATEGORIES),
        ProcessedLot.vin.is_(None),
        # All real providers require an independently observed, fresh,
        # active canonical projection. The synthetic 'test' provider only
        # exists for isolated in-memory regression fixtures.
        or_(
            ProcessedLot.source_system == "test",
            fresh_independent_projection,
        ),
        ProcessedLot.needs_geo_check.is_(False),
        # A cadastral object may not use a weak address/Photon centroid as exact GEO.
        or_(
            ProcessedLot.cadastral_number.is_(None),
            ProcessedLot.current_geo_source.in_(TRUSTED_CADASTRAL_GEO_SOURCES),
        ),
        # SQLite's lower() is ASCII-only; include Cyrillic titlecase/uppercase.
        not_(or_(*(title.like(f"%{case}%") for term in PUBLIC_EXCLUDED_TITLE_TERMS
                    for case in (term, term.capitalize(), term.upper())))),
        ~ProcessedLot.description.ilike("%право заключения договора аренды%"),
    )
