"""Read-only reliability dashboard API."""
from __future__ import annotations

import os
from collections.abc import Callable

from fastapi import APIRouter, Depends, Query
from fastapi.encoders import jsonable_encoder

from bankrotai import __version__
from bankrotai.auth import AuthenticatedUser
from bankrotai.core import get_settings
from bankrotai.db import SCHEMA_REVISION, read_session_scope
from bankrotai.services.reliability import (
    build_reliability_sources,
    build_reliability_summary,
    reliability_map_delivery,
)


def create_reliability_router(
    require_user: Callable[..., AuthenticatedUser],
) -> APIRouter:
    router = APIRouter(prefix="/api/reliability", tags=["Reliability"])
    settings = get_settings()

    @router.get("/summary")
    def summary(_actor: AuthenticatedUser = Depends(require_user)):
        with read_session_scope() as session:
            return jsonable_encoder(
                build_reliability_summary(
                    session,
                    deployment_sha=os.environ.get("BANKROTAI_DEPLOY_SHA", ""),
                    app_version=__version__,
                    schema_revision=SCHEMA_REVISION,
                    api_read_only=settings.api_read_only,
                )
            )

    @router.get("/sources")
    def sources(_actor: AuthenticatedUser = Depends(require_user)):
        with read_session_scope() as session:
            return jsonable_encoder(build_reliability_sources(session))

    @router.get("/map-delivery")
    def map_delivery(
        verify_manifest: bool = Query(True),
        _actor: AuthenticatedUser = Depends(require_user),
    ):
        with read_session_scope() as session:
            return jsonable_encoder(
                reliability_map_delivery(
                    session,
                    verify_manifest=verify_manifest,
                )
            )

    return router
