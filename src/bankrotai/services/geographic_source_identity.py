"""Do not rebuild trusted GEO from legacy imports without source lineage."""
from urllib.parse import urlsplit
import re

from sqlalchemy import and_, or_, select

from bankrotai.db import ProcessedLot, SourceLot
from bankrotai.services.individual_lot_identity import unscoped_procedure_identity

_TB_SOURCES = ("tbankrot", "tbankrot.ru")
_TB_PREFIXES = tuple(scheme+host+"/item" for scheme in ("https://", "http://")
                     for host in ("tbankrot.ru", "www.tbankrot.ru"))


def geographic_source_identity_available(session, lot, result=None):
    source = getattr(lot, "source_system", None)
    external = getattr(lot, "external_id", None)
    if unscoped_procedure_identity(source, external):
        return False
    def card_url(url):
        if not isinstance(url, str):
            return False
        try:
            parsed = urlsplit(url)
        except ValueError:
            return False
        return parsed.hostname in {"tbankrot.ru", "www.tbankrot.ru"} and parsed.path == "/item"
    imported = source in _TB_SOURCES and any(card_url(url) for url in (
        getattr(lot, "lot_url", None), getattr(lot, "source_url", None)))
    if not imported or result is not None and getattr(result, "source", None) == "manual":
        return True
    owner = session.scalar(select(SourceLot).where(
        SourceLot.processed_lot_id == lot.id, SourceLot.source_system == source,
        SourceLot.external_id == external,
    ).limit(1))
    if owner is None:
        return False
    def proven(name, value):
        if value in (None, "") or getattr(owner, name, None) != value:
            return False
        proof = ((owner.raw_data or {}).get("verified_source_fields") or {}).get(name) or {}
        return proof.get("value") == value and bool(re.fullmatch(r"[a-fA-F0-9]{64}", str(proof.get("sha256", ""))))
    if result is not None:
        cad = getattr(result, "cadastral_number", None)
        return proven("cadastral_number", cad) if cad else proven("address", getattr(lot, "address", None))
    return proven("cadastral_number", getattr(lot, "cadastral_number", None)) or proven("address", getattr(lot, "address", None))



def geographic_source_identity_condition():
    """Apply the same check before provider calls, with NULL-safe manual inputs."""
    is_imported = and_(ProcessedLot.source_system.in_(_TB_SOURCES), or_(
        *(or_(column == prefix, column.like(prefix+"?%"), column.like(prefix+"#%")) for column in (ProcessedLot.lot_url, ProcessedLot.source_url)
          for prefix in _TB_PREFIXES),
    ))
    unscoped = or_(*(
        and_(ProcessedLot.source_system == source,
             ProcessedLot.external_id.regexp_match(r"^(?:"+source.replace(".", r"\.")+r":)?\d+$"))
        for source in ("sistematorg.com", "aukcioncenter.ru")
    ))
    def proven(name):
        proof = SourceLot.raw_data["verified_source_fields"][name]
        value = getattr(ProcessedLot, name)
        return and_(value.isnot(None), getattr(SourceLot, name) == value,
                    proof["value"].as_string() == value,
                    proof["sha256"].as_string().regexp_match(r"^[a-fA-F0-9]{64}$"))
    owned = select(SourceLot.id).where(
        SourceLot.processed_lot_id == ProcessedLot.id,
        SourceLot.source_system == ProcessedLot.source_system,
        SourceLot.external_id == ProcessedLot.external_id,
        or_(proven("cadastral_number"), proven("address")),
    ).exists()
    return and_(unscoped.is_not(True), or_(is_imported.is_not(True), owned))
