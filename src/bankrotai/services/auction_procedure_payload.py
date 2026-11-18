"""Read one publication's procedure; never borrow another sale's facts."""
from sqlalchemy import select
from sqlalchemy.orm import Session

from bankrotai.db import ProcessedLot, SourceLot
from bankrotai.services.source_auction_facts import source_auction_date, unverified_auction_fields


def build_lot_procedure(session: Session, lot_id: int) -> dict | None:
    source_lot = session.scalar(select(SourceLot).join(ProcessedLot,
        (SourceLot.source_system == ProcessedLot.source_system)
        & (SourceLot.external_id == ProcessedLot.external_id)
    ).where(ProcessedLot.id == lot_id))
    if source_lot is None:
        return None
    return {
        "source_lot_id": source_lot.id,
        "source_system": source_lot.source_system,
        "platform_name": source_lot.platform_name,
        "platform_code": source_lot.platform_code,
        "procedure_number": source_lot.procedure_number,
        "notice_number": source_lot.notice_number,
        "efresb_message_number": source_lot.efresb_message_number,
        "organizer_name": source_lot.organizer_name,
        "bankruptcy_case_number": source_lot.bankruptcy_case_number,
        "deposit_amount": float(source_lot.deposit_amount) if source_lot.deposit_amount is not None else None,
        "deposit_percent": source_lot.deposit_percent,
        "deposit_deadline": source_lot.deposit_deadline,
        "application_start_at": source_auction_date(source_lot, "application_start_at"),
        "application_deadline": source_auction_date(source_lot, "application_deadline"),
        "auction_at": source_auction_date(source_lot, "auction_at"),
        "unverified_auction_fields": unverified_auction_fields(source_lot),
        "auction_timezone": source_lot.auction_timezone,
        "auction_step_amount": (
            float(source_lot.auction_step_amount) if source_lot.auction_step_amount is not None else None
        ),
        "auction_type": source_lot.auction_type,
        "public_offer_schedule": source_lot.public_offer_schedule,
        "next_interval_price": (
            float(source_lot.next_interval_price) if source_lot.next_interval_price is not None else None
        ),
        "next_price_reduction_at": source_lot.next_price_reduction_at,
        "document_completeness": source_lot.document_completeness,
        "inspection_procedure": source_lot.inspection_procedure,
    }
