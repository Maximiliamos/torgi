"""Keep repair provenance through ordinary source refreshes."""
from copy import deepcopy
from datetime import timezone


_KEYS = ("verified_source_fields", "repair_observed_fields", "repair_evidence_sha256",
         "repair_evidence_checked_at", "ingestion_observed_at", "ingestion_run_id")


def preserve_verification_metadata(incoming, previous):
    result = dict(incoming or {})
    previous = previous if isinstance(previous, dict) else {}
    for key in _KEYS:
        if key not in result and key in previous:
            result[key] = deepcopy(previous[key])
    return result


def ingestion_metadata(incoming, previous, *, run_id, observed_at):
    result = preserve_verification_metadata(incoming, previous)
    observed_at = observed_at.replace(tzinfo=timezone.utc) if observed_at.tzinfo is None else observed_at
    result.update(ingestion_observed_at=observed_at.astimezone(timezone.utc).isoformat(),
                  ingestion_run_id=run_id)
    return result
