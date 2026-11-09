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


SOURCE_CONNECTOR_TYPES = {profile.source_id: connector_type(profile) for profile in PROFILES}

# Vertrades has a verified shared-procedure/per-lot contract that intentionally
# lives outside the generic HTML parser. Register only its source-specific
# connector here; this does not schedule or enable the source for production.
from bankrotai.connectors.etp_sources.vertrades import connector_type as vertrades_connector_type

_vertrades_profile = next(profile for profile in PROFILES if profile.source_id == "vertrades.ru")
SOURCE_CONNECTOR_TYPES["vertrades.ru"] = vertrades_connector_type(_vertrades_profile)

__all__ = [
    "EtpSearchFilters",
    "EtpSourceProfile",
    "HtmlEtpConnector",
    "PROFILES",
    "SOURCE_CONNECTOR_TYPES",
    "SourceAccessError",
    "SourceSchemaError",
    "parse_detail_html",
    "parse_listing_html",
    "parse_sberbank_json",
]
