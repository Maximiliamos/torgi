"""Atomically reconcile DEZSTER public DNS between edge and direct REG.RU modes."""

from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


ZONE_ID = "579174730e5da8c0d9e653d8e45e6900"
ZONE_NAME = "dezster.ru"
DIRECT_IP = "194.226.126.233"
LEGACY_IPV4 = "31.31.196.17"
LEGACY_IPV6 = "2a00:f940:2:2:1:1:0:256"
LEGACY_API_CNAME = "b8060072-d7ea-4faa-90b0-3e8c0612b7ad.cfargotunnel.com"
MANAGED_NAMES = ("dezster.ru", "www.dezster.ru", "api.dezster.ru")


def desired_records(action: str) -> list[dict[str, Any]]:
    if action == "switch":
        return [
            {"name": name, "type": "A", "content": DIRECT_IP, "proxied": False, "ttl": 300}
            for name in MANAGED_NAMES
        ]
    if action == "rollback":
        return [
            {"name": "dezster.ru", "type": "A", "content": LEGACY_IPV4, "proxied": True, "ttl": 1},
            {"name": "dezster.ru", "type": "AAAA", "content": LEGACY_IPV6, "proxied": True, "ttl": 1},
            {"name": "www.dezster.ru", "type": "A", "content": LEGACY_IPV4, "proxied": True, "ttl": 1},
            {"name": "www.dezster.ru", "type": "AAAA", "content": LEGACY_IPV6, "proxied": True, "ttl": 1},
            {
                "name": "api.dezster.ru",
                "type": "CNAME",
                "content": LEGACY_API_CNAME,
                "proxied": True,
                "ttl": 1,
            },
        ]
    raise ValueError(f"unsupported action: {action}")


class CloudflareDns:
    def __init__(self, token: str) -> None:
        self.token = token
        self.base = f"https://api.cloudflare.com/client/v4/zones/{ZONE_ID}/dns_records"

    def request(self, method: str, url: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(
            url,
            data=payload,
            method=method,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                result = json.load(response)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:1000]
            raise RuntimeError(f"Cloudflare DNS {method} failed with HTTP {exc.code}: {detail}") from exc
        if not result.get("success"):
            raise RuntimeError(f"Cloudflare DNS {method} failed: {result.get('errors')}")
        return result

    def records(self) -> list[dict[str, Any]]:
        query = urllib.parse.urlencode({"per_page": 100})
        result = self.request("GET", f"{self.base}?{query}")
        return [record for record in result["result"] if record.get("name") in MANAGED_NAMES]

    def reconcile(self, wanted: list[dict[str, Any]]) -> None:
        wanted_keys = {(item["name"], item["type"]) for item in wanted}
        current = self.records()
        for record in current:
            key = (record["name"], record["type"])
            if key not in wanted_keys:
                self.request("DELETE", f"{self.base}/{record['id']}")

        current = self.records()
        by_key = {(record["name"], record["type"]): record for record in current}
        for item in wanted:
            existing = by_key.get((item["name"], item["type"]))
            if existing:
                self.request("PUT", f"{self.base}/{existing['id']}", item)
            else:
                self.request("POST", self.base, item)


def sanitized(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    fields = ("id", "name", "type", "content", "proxied", "ttl")
    return [{field: record.get(field) for field in fields} for record in records]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--action", choices=("switch", "rollback"), required=True)
    parser.add_argument("--snapshot")
    args = parser.parse_args()
    token = os.environ.get("CLOUDFLARE_API_TOKEN")
    if not token:
        raise SystemExit("CLOUDFLARE_API_TOKEN is required")

    client = CloudflareDns(token)
    before = client.records()
    if args.snapshot:
        Path(args.snapshot).write_text(json.dumps(sanitized(before), indent=2), encoding="utf-8")
    client.reconcile(desired_records(args.action))
    after = sanitized(client.records())
    expected = sanitized(desired_records(args.action))
    for item in expected:
        item.pop("id", None)
    comparable = [{key: value for key, value in item.items() if key != "id"} for item in after]
    if sorted(comparable, key=lambda item: (item["name"], item["type"])) != sorted(
        expected, key=lambda item: (item["name"], item["type"])
    ):
        raise SystemExit("DNS reconciliation did not converge")
    print(json.dumps({"action": args.action, "zone": ZONE_NAME, "records": after}, ensure_ascii=False))


if __name__ == "__main__":
    main()
