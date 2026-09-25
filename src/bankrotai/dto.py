from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class AppDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True)


class SourceHealthDTO(AppDTO):
    source_system: str
    status: str
    items_seen: int = 0
    last_attempt_at: datetime | None = None
    last_success_at: datetime | None = None
    last_complete_success_at: datetime | None = None
    last_failure_at: datetime | None = None
    last_error: str | None = None
    freshness_status: str = "unknown"
    coverage_status: str = "unknown"
    freshness_age_seconds: int | None = None
    complete_snapshot_age_seconds: int | None = None
    last_duration_ms: int | None = None
    last_complete_source_run: bool = False


class DataQualityDTO(AppDTO):
    total_lots: int
    active_lots: int
    archived_lots: int
    duplicate_lots: int
    missing_address: int
    missing_cadastre: int
    missing_price: int
    geocoded_lots: int
    geo_attention_lots: int
    queued_geo_failures: int
    unknown_status_lots: int
    ai_analyzed_lots: int
    document_versions: int


class LotSummaryDTO(AppDTO):
    id: int
    external_id: str
    source_system: str
    title: str
    category: str
    region_slug: str | None
    address: str | None
    current_price: float | None
    auction_status: str
    is_archived: bool
    duplicate_of_id: int | None
    last_update: datetime
