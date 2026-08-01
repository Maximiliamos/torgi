"""Add canonical/source lots, procedure data, documents and participation.

Revision ID: c1d2e3f4a5b6
Revises: b0c1d2e3f4a5
"""
from typing import Sequence, Union
from datetime import datetime

from alembic import op
import sqlalchemy as sa


revision: str = "c1d2e3f4a5b6"
down_revision: Union[str, Sequence[str], None] = "b0c1d2e3f4a5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "canonical_lots",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("canonical_key", sa.String(length=300), nullable=False),
        sa.Column("legacy_processed_lot_id", sa.Integer(), nullable=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("category", sa.String(length=50), nullable=False),
        sa.Column("address", sa.Text(), nullable=True),
        sa.Column("cadastral_number", sa.String(length=50), nullable=True),
        sa.Column("area", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["legacy_processed_lot_id"], ["processed_lots.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("canonical_key"),
        sa.UniqueConstraint("legacy_processed_lot_id"),
    )
    op.create_index("ix_canonical_lots_canonical_key", "canonical_lots", ["canonical_key"])
    op.create_index("ix_canonical_lots_cadastral_number", "canonical_lots", ["cadastral_number"])

    op.create_table(
        "source_lots",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("canonical_lot_id", sa.Integer(), nullable=False),
        sa.Column("processed_lot_id", sa.Integer(), nullable=True),
        sa.Column("source_system", sa.String(length=50), nullable=False),
        sa.Column("external_id", sa.String(length=100), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("platform_name", sa.String(length=300), nullable=True),
        sa.Column("platform_code", sa.String(length=100), nullable=True),
        sa.Column("procedure_number", sa.String(length=150), nullable=True),
        sa.Column("notice_number", sa.String(length=150), nullable=True),
        sa.Column("efresb_message_number", sa.String(length=150), nullable=True),
        sa.Column("debtor_name", sa.String(length=500), nullable=True),
        sa.Column("organizer_name", sa.String(length=500), nullable=True),
        sa.Column("auction_manager_name", sa.String(length=500), nullable=True),
        sa.Column("bankruptcy_case_number", sa.String(length=150), nullable=True),
        sa.Column("deposit_amount", sa.Numeric(15, 2), nullable=True),
        sa.Column("deposit_percent", sa.Float(), nullable=True),
        sa.Column("deposit_payment_details", sa.Text(), nullable=True),
        sa.Column("deposit_deadline", sa.DateTime(), nullable=True),
        sa.Column("application_deadline", sa.DateTime(), nullable=True),
        sa.Column("auction_at", sa.DateTime(), nullable=True),
        sa.Column("auction_step_amount", sa.Numeric(15, 2), nullable=True),
        sa.Column("auction_step_percent", sa.Float(), nullable=True),
        sa.Column("auction_type", sa.String(length=100), nullable=True),
        sa.Column("public_offer_schedule", sa.JSON(), nullable=True),
        sa.Column("next_interval_price", sa.Numeric(15, 2), nullable=True),
        sa.Column("next_price_reduction_at", sa.DateTime(), nullable=True),
        sa.Column("document_completeness", sa.String(length=30), nullable=True),
        sa.Column("inspection_procedure", sa.Text(), nullable=True),
        sa.Column("organizer_contact", sa.Text(), nullable=True),
        sa.Column("raw_data", sa.JSON(), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["canonical_lot_id"], ["canonical_lots.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["processed_lot_id"], ["processed_lots.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("processed_lot_id"),
        sa.UniqueConstraint("source_system", "external_id", name="uq_source_lots_source_external_id"),
    )
    for name in (
        "canonical_lot_id", "processed_lot_id", "source_system", "external_id", "platform_code",
        "procedure_number", "notice_number", "efresb_message_number", "bankruptcy_case_number",
        "deposit_deadline", "application_deadline", "auction_at", "next_price_reduction_at", "last_seen_at",
    ):
        op.create_index(f"ix_source_lots_{name}", "source_lots", [name])

    op.create_table(
        "lot_documents",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("source_lot_id", sa.Integer(), nullable=False),
        sa.Column("external_document_id", sa.String(length=200), nullable=False),
        sa.Column("filename", sa.String(length=500), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("document_kind", sa.String(length=100), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["source_lot_id"], ["source_lots.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_lot_id", "external_document_id", name="uq_lot_documents_source_external"),
    )
    op.create_index("ix_lot_documents_source_lot_id", "lot_documents", ["source_lot_id"])
    op.create_index("ix_lot_documents_document_kind", "lot_documents", ["document_kind"])

    op.create_table(
        "lot_document_versions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("storage_key", sa.Text(), nullable=False),
        sa.Column("mime_type", sa.String(length=200), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=True),
        sa.Column("fetched_at", sa.DateTime(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(["document_id"], ["lot_documents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("document_id", "sha256", name="uq_lot_document_versions_hash"),
    )
    op.create_index("ix_lot_document_versions_document_id", "lot_document_versions", ["document_id"])
    op.create_index("ix_lot_document_versions_sha256", "lot_document_versions", ["sha256"])

    op.create_table(
        "lot_participation_checklists",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("source_lot_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.String(length=100), nullable=False),
        sa.Column("etp_accredited", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("signature_valid", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("application_completed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("deposit_sent", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("payment_purpose_verified", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("deposit_received", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("documents_signed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("application_accepted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["source_lot_id"], ["source_lots.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_lot_id", "user_id", name="uq_participation_source_user"),
    )
    op.create_index("ix_participation_source_lot_id", "lot_participation_checklists", ["source_lot_id"])
    op.create_index("ix_participation_user_id", "lot_participation_checklists", ["user_id"])

    bind = op.get_bind()
    processed_rows = bind.execute(
        sa.text(
            "SELECT id, source_system, external_id, title, category, address, cadastral_number, area, "
            "source_url, last_update FROM processed_lots"
        )
    ).mappings()
    metadata = sa.MetaData()
    canonical = sa.Table("canonical_lots", metadata, autoload_with=bind)
    source = sa.Table("source_lots", metadata, autoload_with=bind)
    canonical_ids: dict[str, int] = {}
    for row in processed_rows:
        timestamp = row["last_update"] or datetime.utcnow()
        if isinstance(timestamp, str):
            timestamp = datetime.fromisoformat(timestamp)
        cadastral = str(row["cadastral_number"] or "").replace(" ", "")
        canonical_key = (
            f"cadastral:{cadastral}"
            if cadastral
            else f"source:{row['source_system']}:{row['external_id']}"
        )
        canonical_id = canonical_ids.get(canonical_key)
        if canonical_id is None:
            result = bind.execute(canonical.insert().values(
                canonical_key=canonical_key,
                legacy_processed_lot_id=row["id"], title=row["title"], category=row["category"],
                address=row["address"], cadastral_number=row["cadastral_number"], area=row["area"],
                created_at=timestamp, updated_at=timestamp,
            ))
            canonical_id = result.inserted_primary_key[0]
            canonical_ids[canonical_key] = canonical_id
        bind.execute(source.insert().values(
            canonical_lot_id=canonical_id, processed_lot_id=row["id"],
            source_system=row["source_system"], external_id=row["external_id"], source_url=row["source_url"],
            last_seen_at=timestamp, created_at=timestamp,
        ))


def downgrade() -> None:
    op.drop_table("lot_participation_checklists")
    op.drop_table("lot_document_versions")
    op.drop_table("lot_documents")
    op.drop_table("source_lots")
    op.drop_table("canonical_lots")
