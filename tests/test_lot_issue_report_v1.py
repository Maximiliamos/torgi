from __future__ import annotations

from types import SimpleNamespace

from pydantic import ValidationError
import pytest

from bankrotai import api
from bankrotai.api_models import LotIssueReportRequest
from bankrotai.db import Base, LotIssueReport


def test_report_error_post_is_allowed_in_read_only_mvp() -> None:
    request = SimpleNamespace(
        method="POST",
        url=SimpleNamespace(path="/api/lots/123/report-error"),
    )
    assert api._is_read_only_mvp_path(request) is True


def test_lot_issue_report_request_is_bounded() -> None:
    assert LotIssueReportRequest(content="Неверная цена").content == "Неверная цена"
    with pytest.raises(ValidationError):
        LotIssueReportRequest(content="")
    with pytest.raises(ValidationError):
        LotIssueReportRequest(content="x" * 5001)


def test_lot_issue_report_table_is_registered() -> None:
    assert "lot_issue_reports" in Base.metadata.tables
    table = Base.metadata.tables["lot_issue_reports"]
    assert set(table.c.keys()) == {"id", "lot_id", "user_id", "content", "created_at"}
    assert LotIssueReport.__tablename__ == "lot_issue_reports"
