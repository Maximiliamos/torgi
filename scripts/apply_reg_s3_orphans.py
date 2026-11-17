"""Revalidate an exact orphan plan; optionally delete only that bounded selection."""
from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

import requests

from plan_reg_s3_orphans import plan_orphans
from s3_capacity_probe import _signed_headers


def validate_selection(plan: dict, inventory: dict, manifests: dict) -> list[str]:
    selected = plan.get("candidate_objects")
    if not isinstance(selected, list) or not 1 <= len(selected) <= 5000:
        raise ValueError("Cleanup requires 1..5000 exact selected objects")
    keys = [item["key"] for item in selected]
    if len(set(keys)) != len(keys) or sum(item["size_bytes"] for item in selected) > 2 * 1024**3:
        raise ValueError("Cleanup selection is duplicated or exceeds 2 GiB")
    observed_hashes = {key: hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
                       for key, value in manifests.items()}
    if observed_hashes != plan["protected_manifests"]:
        raise ValueError("Dataset manifests changed; prepare a new reviewed plan")
    if f"datasets/{plan['active_dataset']}/manifest.json" not in manifests:
        raise ValueError("Active dataset manifest is absent")
    age = plan["minimum_age_hours"]
    if type(age) is not int or age < 24:
        raise ValueError("Cleanup may not target objects younger than 24 hours")
    fresh = plan_orphans(inventory, manifests, now=datetime.now(timezone.utc), minimum_age_hours=age)
    eligible = {item["key"]: item for item in fresh["candidates"]}
    objects = {item["Key"]: item for item in inventory["Contents"]}
    for item in selected:
        key = item["key"]
        if key not in eligible or objects[key]["Size"] != item["size_bytes"] or objects[key]["ETag"] != item["etag"]:
            raise ValueError("Selected immutable object changed or is no longer an orphan")
    return keys


class Client:
    def __init__(self, settings):
        self.settings = settings
        self.session = requests.Session()

    def request(self, method, key="", params=None, body=b"", *, md5=False):
        s = self.settings
        url, headers = _signed_headers(method=method, endpoint=s.map_object_store_endpoint,
            bucket=s.map_object_store_bucket, key=key, params=params,
            payload_hash=hashlib.sha256(body).hexdigest(), access_key=s.map_object_store_access_key,
            secret_key=s.map_object_store_secret_key, region=s.map_object_store_region)
        if md5:
            headers["Content-MD5"] = base64.b64encode(hashlib.md5(body).digest()).decode()
        response = self.session.request(method, url, headers=headers, data=body, timeout=(10, 60))
        if response.status_code != 200:
            raise RuntimeError(f"S3 maintenance {method} failed: HTTP {response.status_code}")
        return response

    def inventory(self):
        objects = []; token = None; seen = set()
        while True:
            params = {"list-type": "2", "max-keys": "1000"}
            if token:
                params["continuation-token"] = token
            root = ET.fromstring(self.request("GET", params=params).content)
            namespace = root.tag.split("}")[0] + "}" if "}" in root.tag else ""
            for node in root.findall(namespace + "Contents"):
                objects.append({"Key": node.findtext(namespace + "Key"), "Size": int(node.findtext(namespace + "Size")),
                    "ETag": node.findtext(namespace + "ETag"), "LastModified": node.findtext(namespace + "LastModified")})
            truncated = root.findtext(namespace + "IsTruncated")
            if truncated == "false":
                break
            token = root.findtext(namespace + "NextContinuationToken")
            if truncated != "true" or not token or token in seen or len(seen) >= 1000:
                raise ValueError("S3 listing completeness is not proved")
            seen.add(token)
        return {"Contents": objects, "IsTruncated": False}

    def manifests(self, inventory):
        return {item["Key"]: self.request("GET", key=item["Key"]).json()
                for item in inventory["Contents"]
                if item["Key"].startswith("datasets/") and item["Key"].endswith("/manifest.json")}

    def delete_exact(self, keys):
        root = ET.Element("Delete")
        for key in keys:
            ET.SubElement(ET.SubElement(root, "Object"), "Key").text = key
        response = self.request("POST", params={"delete": ""}, body=ET.tostring(root, encoding="utf-8"), md5=True)
        parsed = ET.fromstring(response.content)
        errors = [node for node in parsed.iter() if node.tag.rsplit("}", 1)[-1] == "Error"]
        if errors:
            raise RuntimeError(f"S3 reported {len(errors)} per-object deletion errors")
        deleted = {child.text for node in parsed.iter() if node.tag.rsplit("}", 1)[-1] == "Deleted"
                   for child in node if child.tag.rsplit("}", 1)[-1] == "Key"}
        if deleted != set(keys):
            raise RuntimeError("S3 deletion receipt differs from the exact selection")
        return len(deleted)


def execute(plan_bytes: bytes, *, apply=False, expected_sha=None):
    from bankrotai.core import get_settings
    from bankrotai.db import SessionLocal, MapDataset
    from sqlalchemy import select
    from redis import Redis

    digest = hashlib.sha256(plan_bytes).hexdigest()
    if apply and digest != expected_sha:
        raise ValueError("Exact reviewed plan SHA is required")
    plan = json.loads(plan_bytes)
    settings = get_settings()
    redis = Redis.from_url(settings.redis_url, socket_connect_timeout=2, socket_timeout=2)
    lock = redis.lock("bankrotai:map-dataset-build", timeout=900, blocking_timeout=0)
    if not lock.acquire(blocking=False):
        raise RuntimeError("Map publisher is active; refuse S3 cleanup")
    try:
        with SessionLocal() as session:
            current = session.scalar(select(MapDataset.version).where(MapDataset.is_current.is_(True)))
        if current != plan["active_dataset"]:
            raise ValueError("Active database map version changed")
        client = Client(settings)
        inventory = client.inventory()
        manifests = client.manifests(inventory)
        keys = validate_selection(plan, inventory, manifests)
        deleted = client.delete_exact(keys) if apply else 0
        # Current and rollback manifests must remain byte-equivalent after deletion.
        if apply and client.manifests(inventory) != manifests:
            raise RuntimeError("Protected manifest verification failed after cleanup")
        return {"apply": apply, "validated_objects": len(keys), "deleted_objects": deleted,
                "selected_bytes": sum(item["size_bytes"] for item in plan["candidate_objects"]),
                "protected_manifests": len(manifests), "plan_sha256": digest}
    finally:
        lock.release()
        redis.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--expected-sha")
    args = parser.parse_args()
    print(json.dumps(execute(args.plan.read_bytes(), apply=args.apply, expected_sha=args.expected_sha)))


if __name__ == "__main__":
    main()
