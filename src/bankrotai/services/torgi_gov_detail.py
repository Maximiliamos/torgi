from __future__ import annotations

import re
from typing import Any, Type


def fetch_torgi_gov_lot_detail(
    client: Any,
    external_id: str,
    *,
    error_type: Type[RuntimeError],
):
    """Fetch one official public lot detail and prove exact source identity."""
    raw_id = str(external_id or "").strip()
    if raw_id.startswith("torgi_gov:"):
        raw_id = raw_id.split(":", 1)[1]
    if not re.fullmatch(r"[0-9]+_[0-9]+", raw_id):
        raise error_type("Invalid torgi.gov lot identity")

    client._respect_rate_limit()
    response = client.session.get(
        f"{client.base_url}/new/api/public/lotcards/{raw_id}",
        timeout=client.timeout,
    )
    response.raise_for_status()
    try:
        payload = response.json()
    except ValueError as exc:
        raise error_type("torgi.gov detail returned non-JSON response") from exc
    if not isinstance(payload, dict):
        raise error_type("torgi.gov detail returned unexpected payload type")

    observed = client._external_base_from_payload(payload)
    if observed != raw_id:
        raise error_type(
            f"torgi.gov detail identity mismatch: expected {raw_id}, got {observed or 'missing'}"
        )

    lot = client._normalize_api_lot(payload)
    if lot.external_id != f"torgi_gov:{raw_id}":
        raise error_type("torgi.gov normalized detail identity mismatch")
    raw = dict(lot.raw_data or {})
    raw["source"] = "torgi_gov_detail_json"
    raw["detail_enrichment_status"] = "success"
    lot.raw_data = raw
    lot.detail_level = "detail"
    return lot
