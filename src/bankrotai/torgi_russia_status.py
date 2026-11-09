from __future__ import annotations


def public_auction_status(source_status: object, *, history_only: bool = False) -> str:
    """Normalize provider status conservatively for public sale eligibility."""
    if history_only:
        return "archived"
    if isinstance(source_status, dict):
        source_status = (
            source_status.get("title")
            or source_status.get("name")
            or source_status.get("status")
            or ""
        )
    value = str(source_status or "").strip().casefold().replace("ё", "е")
    if not value:
        return "unknown"
    if any(marker in value for marker in (
        "заверш", "окончен", "закрыт", "отмен", "прекращ", "не состоял",
        "архив", "снят с торгов", "completed", "finished", "closed",
        "cancelled", "canceled", "expired", "failed",
    )):
        return "closed"
    if any(marker in value for marker in (
        "прием заяв", "идет прием", "идут торги", "торги идут",
        "объявлен", "проводятся торги", "опубликован", "scheduled",
        "active", "published", "open", "applications_submission",
    )):
        return "active"
    return "unknown"
