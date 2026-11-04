"""Fail-closed ETP enrollment for the existing 09:00/21:00 Moscow schedule.

All 21 direct profiles are visible to scheduled READ-ONLY QA. The existing
Lot-online source remains in default nationwide ingestion. A new ETP enters
production ingestion only after a separately reviewed code change naming its
source and showing independently verified full-catalogue and detail evidence.
An HTTP-accessible profile is not an operator acceptance.
"""
from __future__ import annotations

from bankrotai.connectors.etp_sources import PROFILES, SOURCE_CONNECTOR_TYPES

BASELINE_SOURCE = "lot-online.ru"
ALL_ETP_SOURCE_IDS = (BASELINE_SOURCE,) + tuple(profile.source_id for profile in PROFILES)

# Deliberately empty. This is a *production* allowlist, not a list of
# registered or browser-accessible sources. Add one source per reviewed PR
# only after two full read-only inventories, detail and last-page verification,
# restore-tested backup, collision audit, and exact-SHA acceptance.
ACCEPTED_ETP_FOR_AUTOMATIC_INGEST: frozenset[str] = frozenset()


def accepted_etp_source_specs() -> tuple:
    """Build non-archiving, source-end-required specs for approved providers.

    Validation is repeated on each call: configuration drift or accidental
    admission of an unverified adapter must fail before any DB writes.
    """
    from bankrotai.connectors.etp_sources import EtpSearchFilters
    from bankrotai.services.ingestion import SourceSyncSpec

    profiles = {profile.source_id: profile for profile in PROFILES}
    unknown = ACCEPTED_ETP_FOR_AUTOMATIC_INGEST - profiles.keys()
    if unknown:
        raise ValueError("Unknown ETP production approvals: " + ",".join(sorted(unknown)))
    specs = []
    for source_id in sorted(ACCEPTED_ETP_FOR_AUTOMATIC_INGEST):
        profile = profiles[source_id]
        if profile.blocked_reason:
            raise ValueError(f"{source_id}: source is blocked; cannot auto-import")
        if "detail_enrichment" not in SOURCE_CONNECTOR_TYPES[source_id].capabilities:
            raise ValueError(f"{source_id}: independent per-lot detail is not enforced")
        if not profile.supports_get_pagination and not (
            profile.form_page_field or profile.postback_pagination or profile.api_kind
        ):
            raise ValueError(f"{source_id}: no verified pagination implementation")
        specs.append(SourceSyncSpec(
            source_id, EtpSearchFilters(page=1), reconcile_missing=False,
            require_source_end=True, max_batches=3500,
        ))
    return tuple(specs)


def scheduled_source_inventory() -> dict:
    """Single authoritative inventory; never represents QA as production sync."""
    return {
        "total_etps": len(ALL_ETP_SOURCE_IDS),
        "baseline": BASELINE_SOURCE,
        "direct_etp_profiles": len(PROFILES),
        "read_only_qa_sources": tuple(profile.source_id for profile in PROFILES),
        "approved_automatic_ingest": tuple(sorted(ACCEPTED_ETP_FOR_AUTOMATIC_INGEST)),
        "blocked_profiles": tuple(
            profile.source_id for profile in PROFILES if profile.blocked_reason
        ),
        "full_inventory_accepted_by_qa": False,
    }
