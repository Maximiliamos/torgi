#!/usr/bin/env python3
"""Sanitized read-only probe for the official ETPU catalogue filter form."""
from __future__ import annotations

import json

from bankrotai.connectors.etp_sources.etpu import EtpuClient, SOURCE_ID


def main() -> int:
    contract = EtpuClient().fetch_filter_contract()
    report = {
        "source": SOURCE_ID,
        "mode": "public_read_only",
        "production_enabled": False,
        "filter_contract": contract,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
