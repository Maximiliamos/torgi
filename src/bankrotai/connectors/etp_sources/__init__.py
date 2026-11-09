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
from bankrotai.connectors.etp_sources.fabrikant_connector import FabrikantConnector
from bankrotai.connectors.etp_sources.profiles import PROFILES


SOURCE_CONNECTOR_TYPES = {profile.source_id: connector_type(profile) for profile in PROFILES}
_fabrikant_profile = next(profile for profile in PROFILES if profile.source_id == "fabrikant.ru")
SOURCE_CONNECTOR_TYPES["fabrikant.ru"] = type(
    "FabrikantSourceConnector",
    (FabrikantConnector,),
    {"profile": _fabrikant_profile},
)

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
