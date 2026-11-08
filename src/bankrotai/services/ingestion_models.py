"""Small value objects shared by nationwide ingestion orchestration."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class SourceSyncSpec:
    source_id: str
    filters: Any
    archive_region_code: str | None = None
    reconcile_missing: bool = True
    max_batches: int | None = None
    require_source_end: bool = False
    max_detail_attempts_per_page: int | None = None
    allow_checkpoint_resume: bool = False


@dataclass(slots=True)
class SourceSyncResult:
    source_system: str
    status: str = "running"
    complete_source_run: bool = False
    pages_scanned: int = 0
    items_seen: int = 0
    items_inserted: int = 0
    items_updated: int = 0
    items_unchanged: int = 0
    items_archived: int = 0
    items_failed: int = 0
    items_rejected: int = 0
    quarantined_records: int = 0
    quarantine_reasons: dict[str, int] = field(default_factory=dict)
    quarantine_samples: list[dict[str, Any]] = field(default_factory=list)
    items_duplicates: int = 0
    duplicates_merged: int = 0
    category_pages: dict[str, int] = field(default_factory=dict)
    timing_ms: dict[str, float] = field(default_factory=dict)
    sql_statements: int = 0
    http_requests: int = 0
    response_bytes: int = 0
    catalogue_candidates: int = 0
    detail_attempts: int = 0
    detail_requests_saved: int = 0
    retry_count: int = 0
    eta_seconds: float | None = None
    elapsed_seconds: float = 0.0
    current_category: str | None = None
    total_pages: int | None = None
    progress_current: int | None = None
    progress_total: int | None = None
    phase: str = "scanning"
    error: str | None = None
    filter_signature: str | None = None
    resume_cursor: str | None = None
    connector_checkpoint: dict[str, Any] | None = None
    resumed_from_run_id: str | None = None
    seen_external_ids: set[str] = field(default_factory=set, repr=False)
