"""P13: one conservative public map policy shared by builders and read models.

Only current real-estate sale candidates may be presented as public map points.
The historical registry retains excluded rows for audit and recovery.
"""

from __future__ import annotations

from sqlalchemy import not_, or_

from bankrotai.db import ProcessedLot
from bankrotai.services.real_estate_filter import REAL_ESTATE_CATEGORIES

PUBLIC_ACTIVE_STATUSES = frozenset({
    "active", "scheduled", "published", "open", "applications_submission",
})
# Unknown categories/statuses are ineligible, even when is_archived is stale.
PUBLIC_EXCLUDED_TITLE_TERMS = (
    "аренд", "субаренд", "договор найма", "право пользования",
    "мопед", "мотоцикл", "автомобил", "транспортн", "прицеп",
    "грузовик", "спецтехник", "катер", "лодк", "снегоход",
)


def public_map_predicates() -> tuple:
    """Composable SQLAlchemy WHERE clauses: no in-memory post-limit filtering."""
    title = ProcessedLot.title
    return (
        ProcessedLot.duplicate_of_id.is_(None),
        ProcessedLot.is_archived.is_(False),
        ProcessedLot.auction_status.in_(PUBLIC_ACTIVE_STATUSES),
        ProcessedLot.category.in_(REAL_ESTATE_CATEGORIES),
        ProcessedLot.vin.is_(None),
        ProcessedLot.needs_geo_check.is_(False),
        not_(or_(*(title.ilike(f"%{term}%") for term in PUBLIC_EXCLUDED_TITLE_TERMS))),
        ~ProcessedLot.description.ilike("%право заключения договора аренды%"),
    )
