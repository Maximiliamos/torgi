from __future__ import annotations

from bankrotai.connectors.etp_sources.common import (
    EtpSearchFilters,
    EtpSourceProfile,
    HtmlEtpConnector,
    SourceAccessError,
    SourceSchemaError,
    connector_type,
    parse_detail_html,
    parse_listing_html,
    parse_sberbank_json,
)
from bankrotai.connectors.etp_sources.profiles import PROFILES
from bankrotai.connectors.etp_sources.zakazrf import ZakazRfConnector


SOURCE_CONNECTOR_TYPES = {profile.source_id: connector_type(profile) for profile in PROFILES}
# ZakazRF owns a source-specific contract; registration changes the pilot/QA
# adapter only. It does not add the source to default_source_specs().
SOURCE_CONNECTOR_TYPES["zakazrf.ru"] = ZakazRfConnector

__all__ = [
    "EtpSearchFilters",
    "EtpSourceProfile",
    "HtmlEtpConnector",
    "PROFILES",
    "SOURCE_CONNECTOR_TYPES",
    "SourceAccessError",
    "SourceSchemaError",
    "ZakazRfConnector",
    "parse_detail_html",
    "parse_listing_html",
    "parse_sberbank_json",
]
