"""Pure request validation models for the BankrotAI HTTP API (BAT-308)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class BulkTorgiSyncRequest(BaseModel):
    search: str = Field("", max_length=200)
    region: str = Field("", max_length=200)
    category: str = Field("", max_length=100)
    price_min: float | None = Field(None, ge=0)
    price_max: float | None = Field(None, ge=0)
    notice_status: str | None = Field(None, max_length=100)
    lot_status: str | None = Field(None, max_length=100)
    max_items: int = Field(10_000, ge=1, le=50_000)


class TBankrotBrowserClickRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    x: float = Field(ge=0, le=1280)
    y: float = Field(ge=0, le=760)


class TBankrotBrowserTypeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(max_length=512)


class TBankrotBrowserKeyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=32)


class TBankrotBrowserScrollRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    delta_y: float = Field(ge=-2000, le=2000)


class MaxBidRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenario_name: str | None = Field(None, max_length=200)
    conservative_sale_price: float = Field(gt=0)
    repair_cost: float = Field(0, ge=0)
    legal_cost: float = Field(0, ge=0)
    monthly_holding_cost: float = Field(0, ge=0)
    holding_months: float = Field(6, gt=0, le=120)
    taxes: float = Field(0, ge=0)
    sale_commission_percent: float = Field(0, ge=0, le=100)
    target_profit: float = Field(0, ge=0)
    risk_reserve: float = Field(0, ge=0)
    annual_capital_cost_percent: float = Field(0, ge=0, le=100)
    intended_bid: float | None = Field(None, ge=0)


class ParticipationChecklistRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    etp_accredited: bool = False
    signature_valid: bool = False
    application_completed: bool = False
    deposit_sent: bool = False
    payment_purpose_verified: bool = False
    deposit_received: bool = False
    documents_signed: bool = False
    application_accepted: bool = False
    notes: str | None = Field(None, max_length=5000)


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=500)


class NoteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=1, max_length=10_000)


class SavedSearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field("", max_length=200)
    query: dict[str, Any]


class DuplicateMergeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    secondary_lot_id: int = Field(gt=0)
    reason: str = Field("", max_length=2000)


class DuplicateSplitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field("", max_length=2000)


class ReviewStatusRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str | None = Field(None, pattern="^(approved|maybe|rejected)$")


class DocumentCompareRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    from_version_id: int = Field(gt=0)
    to_version_id: int = Field(gt=0)


class OnlineLotImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    external_id: str = Field(min_length=1, max_length=100)
    source: str = Field(min_length=1, max_length=50)
    source_system: str = Field(min_length=1, max_length=50)
    title: str = Field(min_length=1, max_length=5000)
    description: str = Field("", max_length=50_000)
    category: str = Field("other", max_length=100)
    region_slug: str | None = Field(None, max_length=100)
    region_name: str | None = Field(None, max_length=200)
    address: str | None = Field(None, max_length=5000)
    cadastral_number: str | None = Field(None, max_length=100)
    current_price: float | None = Field(None, ge=0)
    start_price: float | None = Field(None, ge=0)
    auction_status: str = Field("unknown", max_length=100)
    lot_url: str | None = Field(None, max_length=5000)
    source_url: str | None = Field(None, max_length=5000)
    published_at: datetime | None = None
