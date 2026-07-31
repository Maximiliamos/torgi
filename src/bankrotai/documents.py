from __future__ import annotations

import hashlib
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from bankrotai.db import LotDocument, LotDocumentVersion


def record_document_version(
    session: Session,
    *,
    source_lot_id: int,
    external_document_id: str,
    filename: str,
    content: bytes,
    storage_key: str,
    source_url: str | None = None,
    document_kind: str | None = None,
    mime_type: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> tuple[LotDocument, LotDocumentVersion, bool]:
    """Record an immutable document version; returns created_version as the third value."""

    digest = hashlib.sha256(content).hexdigest()
    document = session.scalar(
        select(LotDocument).where(
            LotDocument.source_lot_id == source_lot_id,
            LotDocument.external_document_id == external_document_id,
        )
    )
    if document is None:
        document = LotDocument(
            source_lot_id=source_lot_id,
            external_document_id=external_document_id,
            filename=filename,
            source_url=source_url,
            document_kind=document_kind,
        )
        session.add(document)
        session.flush()
    else:
        document.filename = filename
        document.source_url = source_url or document.source_url
        document.document_kind = document_kind or document.document_kind

    version = session.scalar(
        select(LotDocumentVersion).where(
            LotDocumentVersion.document_id == document.id,
            LotDocumentVersion.sha256 == digest,
        )
    )
    if version is not None:
        return document, version, False

    version = LotDocumentVersion(
        document_id=document.id,
        sha256=digest,
        storage_key=storage_key,
        mime_type=mime_type,
        size_bytes=len(content),
        metadata_json=metadata,
    )
    session.add(version)
    session.flush()
    return document, version, True
