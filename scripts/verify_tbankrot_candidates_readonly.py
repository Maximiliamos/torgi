"""Recheck exact candidate IDs against public TBankrot details, without DB writes.

No browser cookies, credentials, paid-access workarounds or reconciliation.
Absent fields remain unverified. JSONL journals support bounded resumption.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re

import requests

from bankrotai.connectors.registry.detail_transport import DetailWorkers
from bankrotai.services.asset_category import classify_category
from bankrotai.services.map_property_eligibility import map_property_eligible
from bankrotai.tbankrot_detail import parse_tbankrot_detail_html


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--requests-per-second", type=float, default=1.0)
    args = parser.parse_args()
    payload = json.loads(Path(args.input).read_text(encoding="utf-8-sig"))
    items = payload["items"] if isinstance(payload, dict) else payload
    targets = {item["external_id"]: item for item in items if item["source_system"] == "tbankrot.ru"}
    if len(targets) > 10000 or any(not re.fullmatch(r"tbankrot:\d+", key) for key in targets):
        raise ValueError("Invalid exact TBankrot candidate scope")
    if not 0 < args.requests_per_second <= 2:
        raise ValueError("Public verification rate must be at most two requests per second")
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    captures = output.parent / "tbankrot-public-candidate-html"
    captures.mkdir(exist_ok=True)
    completed = set()
    if output.exists():
        completed = {row["external_id"] for row in map(json.loads, output.read_text(encoding="utf8").splitlines())
                     if row.get("identity_verified") or row.get("source_unavailable")}
    consecutive_network_errors = 0
    consecutive_schema_errors = 0
    with requests.Session() as client:
        client.headers["User-Agent"] = "Mozilla/5.0 BankrotAI-source-verification"
        workers = DetailWorkers(client, concurrency=2, rate=args.requests_per_second)

        def verify(target):
            identity, item = target
            url = "https://tbankrot.ru/item?id=" + identity.split(":", 1)[1]
            workers.acquire(url)
            result = {"external_id": identity, "lot_id": item["id"], "source_url": url,
                      "database_writes": 0, "checked_at_utc": datetime.now(timezone.utc).isoformat()}
            try:
                with workers.session().get(url, timeout=(5, 25), stream=True, allow_redirects=False) as response:
                    result["http_status"] = response.status_code
                    if response.status_code in {401, 403, 429}:
                        result["source_refused"] = True
                    if response.status_code in {404, 410}:
                        result["source_unavailable"] = True
                        return result
                    response.raise_for_status()
                    if response.is_redirect:
                        raise ValueError("Public detail redirected; identity not verified")
                    body = bytearray()
                    for block in response.iter_content(65536):
                        body.extend(block)
                        if len(body) > 4 * 1024 * 1024:
                            raise ValueError("Public detail exceeds bounded evidence size")
                    html = body.decode("utf-8", errors="replace")
                digest = hashlib.sha256(body).hexdigest()
                (captures / f"{identity.split(':')[1]}-{digest}.html").write_bytes(body)
                result["sha256"] = digest
                fields = parse_tbankrot_detail_html(html, url)
                if not fields.get("detail_identity_verified"):
                    raise ValueError("Exact detail panel identity is absent")
                fields["category"] = classify_category(fields.get("title") or "", fields.get("description") or "")
                result.update(fields=fields, sha256=digest, identity_verified=True,
                              eligible_by_sale_property_policy=map_property_eligible(fields),
                              observed_fields=[key for key, value in fields.items()
                                               if value not in (None, "", [], "unknown")],
                              access_limited="free_activation_field" in html)
            except requests.RequestException as exc:
                result.update(error_type=type(exc).__name__, error=str(exc)[:300], network_error=True)
            except ValueError as exc:
                result.update(error_type=type(exc).__name__, error=str(exc), schema_error=True)
            return result

        pending = [(identity, item) for identity, item in sorted(targets.items()) if identity not in completed]
        done = len(set(targets) & completed)
        with workers.pool() as executor:
            for result in workers.map_ordered(executor, verify, pending):
                done += 1
                consecutive_network_errors = consecutive_network_errors + 1 if result.get("network_error") else 0
                consecutive_schema_errors = consecutive_schema_errors + 1 if result.get("schema_error") else 0
                stop = result.get("source_refused") or consecutive_network_errors >= 2 or consecutive_schema_errors >= 3
                with output.open("a", encoding="utf8") as handle:
                    handle.write(json.dumps(result, ensure_ascii=True, default=str) + "\n")
                print(json.dumps({"progress": done, "targets": len(targets), "external_id": result["external_id"],
                                  "verified": result.get("identity_verified", False), "stop": bool(stop)}), flush=True)
                if stop:
                    raise SystemExit("Source access/network/schema failure; remaining candidates stay unverified")


if __name__ == "__main__":
    main()
