from __future__ import annotations

import hashlib
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from bankrotai.db import LotDocument, LotDocumentChange, LotDocumentVersion


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


def compare_document_versions(
    session: Session,
    from_version_id: int,
    to_version_id: int,
) -> LotDocumentChange:
    before = session.get(LotDocumentVersion, from_version_id)
    after = session.get(LotDocumentVersion, to_version_id)
    if before is None or after is None:
        raise ValueError("Both document versions must exist")
    if before.document_id != after.document_id:
        raise ValueError("Document versions belong to different documents")
    if before.id == after.id:
        raise ValueError("Choose two different document versions")
    existing = session.scalar(select(LotDocumentChange).where(
        LotDocumentChange.from_version_id == before.id,
        LotDocumentChange.to_version_id == after.id,
    ))
    if existing is not None:
        return existing
    before_meta = before.metadata_json or {}
    after_meta = after.metadata_json or {}
    changed_metadata = {
        key: {"before": before_meta.get(key), "after": after_meta.get(key)}
        for key in sorted(set(before_meta) | set(after_meta))
        if before_meta.get(key) != after_meta.get(key)
    }
    summary = {
        "content_changed": before.sha256 != after.sha256,
        "size": {"before": before.size_bytes, "after": after.size_bytes},
        "mime_type": {"before": before.mime_type, "after": after.mime_type},
        "metadata_changes": changed_metadata,
    }
    change = LotDocumentChange(
        document_id=before.document_id,
        from_version_id=before.id,
        to_version_id=after.id,
        summary_json=summary,
    )
    session.add(change)
    session.flush()
    return change
