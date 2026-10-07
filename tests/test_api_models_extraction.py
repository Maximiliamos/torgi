"""Behavior-preserving validation for extracted Pydantic API request models."""

import pytest
from pydantic import ValidationError

from bankrotai import api
from bankrotai.api_models import (
    BulkTorgiSyncRequest,
    DocumentCompareRequest,
    LoginRequest,
    ReviewStatusRequest,
    TBankrotBrowserClickRequest,
)


def test_public_api_imports_preserve_model_identity() -> None:
    assert api.LoginRequest is LoginRequest
    assert api.BulkTorgiSyncRequest is BulkTorgiSyncRequest
    assert api.ReviewStatusRequest is ReviewStatusRequest
    assert api.DocumentCompareRequest is DocumentCompareRequest


def test_login_rejects_invalid_empty_password() -> None:
    with pytest.raises(ValidationError):
        LoginRequest(username="reader", password="")


def test_bulk_sync_retains_hard_item_limit() -> None:
    assert BulkTorgiSyncRequest().max_items == 10_000
    with pytest.raises(ValidationError):
        BulkTorgiSyncRequest(max_items=50_001)


def test_tbankrot_browser_is_bounded_and_forbids_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        TBankrotBrowserClickRequest(x=-1, y=10)
    with pytest.raises(ValidationError):
        TBankrotBrowserClickRequest(x=5, y=5, injected="no")


def test_document_compare_and_review_status_are_strict() -> None:
    with pytest.raises(ValidationError):
        DocumentCompareRequest(from_version_id=0, to_version_id=1)
    with pytest.raises(ValidationError):
        ReviewStatusRequest(status="admin")
