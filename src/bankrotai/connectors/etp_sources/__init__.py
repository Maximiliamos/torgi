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
