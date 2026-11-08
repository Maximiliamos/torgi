"""Minimal authenticated lot error reporting API."""

from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter, Depends, HTTPException

from bankrotai.api_models import LotIssueReportRequest
from bankrotai.auth import AuthenticatedUser
from bankrotai.db import LotIssueReport, ProcessedLot, session_scope
from bankrotai.logic import log_action


def create_lot_issue_report_router(
    require_user: Callable[..., AuthenticatedUser],
) -> APIRouter:
    router = APIRouter()

    @router.post("/api/lots/{lot_id}/report-error", status_code=201)
    def report_lot_error(
        lot_id: int,
        request: LotIssueReportRequest,
        actor: AuthenticatedUser = Depends(require_user),
    ):
        content = request.content.strip()
        if not content:
            raise HTTPException(status_code=422, detail="Описание ошибки не может быть пустым")
        with session_scope() as session:
            if session.get(ProcessedLot, lot_id) is None:
                raise HTTPException(status_code=404, detail="Lot not found")
            report = LotIssueReport(
                lot_id=lot_id,
                user_id=str(actor.id),
                content=content,
            )
            session.add(report)
            session.flush()
            log_action(
                session,
                str(actor.id),
                "report_lot_error",
                "lot",
                str(lot_id),
                {"report_id": report.id},
            )
            return {
                "id": report.id,
                "lot_id": lot_id,
                "content": report.content,
                "created_at": report.created_at,
            }

    return router
