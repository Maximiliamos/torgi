"""Direct S3 write-capacity probe using a temporary multipart object.

The probe proves that the configured bucket can accept the requested number of
new bytes at probe time. The temporary object or incomplete multipart upload is
removed before exit.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import math
import sys
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

import requests

_SERVICE = "s3"
_MIN_MULTIPART_PART = 5 * 1024 * 1024
_DEFAULT_PART_SIZE = 8 * 1024 * 1024
_DEFAULT_REQUIRED_BYTES = 512 * 1024 * 1024


def _signing_key(secret: str, date_stamp: str, region: str) -> bytes:
    date_key = hmac.new(("AWS4" + secret).encode(), date_stamp.encode(), hashlib.sha256).digest()
    region_key = hmac.new(date_key, region.encode(), hashlib.sha256).digest()
    service_key = hmac.new(region_key, _SERVICE.encode(), hashlib.sha256).digest()
    return hmac.new(service_key, b"aws4_request", hashlib.sha256).digest()


def _canonical_query(params: dict[str, str] | None) -> str:
    if not params:
        return ""
    encoded = [
        (quote(str(key), safe="-_.~"), quote(str(value), safe="-_.~"))
        for key, value in params.items()
    ]
    encoded.sort()
    return "&".join(f"{key}={value}" for key, value in encoded)


def _canonical_object_url(endpoint: str, bucket: str, key: str,
                          params: dict[str, str] | None = None) -> tuple[str, str, str, str]:
    parsed = urlsplit(endpoint.rstrip("/"))
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("S3 endpoint must be an absolute HTTP(S) URL")
    bucket_part = quote(bucket, safe="-_.~")
    key_part = quote(key.lstrip("/"), safe="/-_.~")
    base_path = parsed.path.rstrip("/")
    canonical_uri = f"{base_path}/{bucket_part}/{key_part}" or "/"
    canonical_query = _canonical_query(params)
    return (
        urlunsplit((parsed.scheme, parsed.netloc, canonical_uri, canonical_query, "")),
        parsed.netloc,
        canonical_uri,
        canonical_query,
    )


def _signed_headers(*, method: str, endpoint: str, bucket: str, key: str,
                    params: dict[str, str] | None, payload_hash: str,
                    access_key: str, secret_key: str, region: str,
                    now: datetime | None = None) -> tuple[str, dict[str, str]]:
    current = now or datetime.now(timezone.utc)
    amz_date = current.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = current.strftime("%Y%m%d")
    url, host, canonical_uri, canonical_query = _canonical_object_url(
        endpoint, bucket, key, params
    )
    headers = {
        "host": host,
        "x-amz-content-sha256": payload_hash,
        "x-amz-date": amz_date,
    }
    signed_header_names = ";".join(sorted(headers))
    canonical_headers = "".join(
        f"{name}:{headers[name].strip()}\n" for name in sorted(headers)
    )
    canonical_request = "\n".join([
        method.upper(), canonical_uri, canonical_query, canonical_headers,
        signed_header_names, payload_hash,
    ])
    credential_scope = f"{date_stamp}/{region}/{_SERVICE}/aws4_request"
    string_to_sign = "\n".join([
        "AWS4-HMAC-SHA256", amz_date, credential_scope,
        hashlib.sha256(canonical_request.encode()).hexdigest(),
    ])
    signature = hmac.new(
        _signing_key(secret_key, date_stamp, region),
        string_to_sign.encode(), hashlib.sha256,
    ).hexdigest()
    headers["authorization"] = (
        "AWS4-HMAC-SHA256 "
        f"Credential={access_key}/{credential_scope}, "
        f"SignedHeaders={signed_header_names}, "
        f"Signature={signature}"
    )
    return url, {name.title(): value for name, value in headers.items()}


def _request(session: requests.Session, *, method: str, endpoint: str,
             bucket: str, key: str, params: dict[str, str] | None,
             body: bytes, access_key: str, secret_key: str, region: str,
             expected_statuses: set[int], timeout_seconds: int) -> requests.Response:
    url, headers = _signed_headers(
        method=method, endpoint=endpoint, bucket=bucket, key=key, params=params,
        payload_hash=hashlib.sha256(body).hexdigest(), access_key=access_key,
        secret_key=secret_key, region=region,
    )
    response = session.request(
        method, url, data=body if method not in {"HEAD", "DELETE"} else None,
        headers=headers, timeout=(10, timeout_seconds),
    )
    if response.status_code not in expected_statuses:
        detail = (response.text or "")[:500].replace("\n", " ")
        raise RuntimeError(
            f"S3 {method} failed for capacity probe: HTTP {response.status_code} {detail}"
        )
    return response


def _upload_id(response: requests.Response) -> str:
    root = ET.fromstring(response.content)
    for node in root.iter():
        if node.tag.rsplit("}", 1)[-1] == "UploadId" and node.text:
            return node.text
    raise RuntimeError("S3 multipart initiation did not return UploadId")


def _complete_body(etags: list[str]) -> bytes:
    root = ET.Element("CompleteMultipartUpload")
    for index, etag in enumerate(etags, start=1):
        part = ET.SubElement(root, "Part")
        ET.SubElement(part, "PartNumber").text = str(index)
        ET.SubElement(part, "ETag").text = etag
    return ET.tostring(root, encoding="utf-8")


def probe_capacity(*, endpoint: str, bucket: str, access_key: str,
                   secret_key: str, region: str,
                   required_bytes: int = _DEFAULT_REQUIRED_BYTES,
                   part_size: int = _DEFAULT_PART_SIZE,
                   timeout_seconds: int = 120,
                   session: requests.Session | None = None) -> dict[str, Any]:
    if required_bytes < _MIN_MULTIPART_PART:
        raise ValueError("required_bytes must be at least 5 MiB")
    if part_size < _MIN_MULTIPART_PART:
        raise ValueError("part_size must be at least 5 MiB")
    if part_size > 64 * 1024 * 1024:
        raise ValueError("part_size must be at most 64 MiB")
    part_count = math.ceil(required_bytes / part_size)
    if part_count > 10_000:
        raise ValueError("multipart probe would exceed 10,000 parts")
    if not endpoint or not bucket or not access_key or not secret_key or not region:
        raise ValueError("complete S3 configuration is required")

    client = session or requests.Session()
    key = f"diagnostics/capacity-probe/{uuid.uuid4().hex}.bin"
    upload_id: str | None = None
    completed = False
    deleted = False
    started = datetime.now(timezone.utc)
    try:
        init = _request(
            client, method="POST", endpoint=endpoint, bucket=bucket, key=key,
            params={"uploads": ""}, body=b"", access_key=access_key,
            secret_key=secret_key, region=region, expected_statuses={200},
            timeout_seconds=timeout_seconds,
        )
        upload_id = _upload_id(init)
        etags: list[str] = []
        zero_part = bytes(part_size)
        remaining = required_bytes
        for index in range(1, part_count + 1):
            size = min(part_size, remaining)
            body = zero_part if size == part_size else bytes(size)
            response = _request(
                client, method="PUT", endpoint=endpoint, bucket=bucket, key=key,
                params={"partNumber": str(index), "uploadId": upload_id},
                body=body, access_key=access_key, secret_key=secret_key,
                region=region, expected_statuses={200},
                timeout_seconds=timeout_seconds,
            )
            etag = response.headers.get("ETag") or response.headers.get("etag")
            if not etag:
                raise RuntimeError(f"S3 multipart part {index} returned no ETag")
            etags.append(etag)
            remaining -= size
            if index == 1 or index == part_count or index % 8 == 0:
                print(f"S3_CAPACITY_PROGRESS={index}/{part_count}",
                      file=sys.stderr, flush=True)

        complete_body = _complete_body(etags)
        _request(
            client, method="POST", endpoint=endpoint, bucket=bucket, key=key,
            params={"uploadId": upload_id}, body=complete_body,
            access_key=access_key, secret_key=secret_key, region=region,
            expected_statuses={200}, timeout_seconds=timeout_seconds,
        )
        completed = True
        head = _request(
            client, method="HEAD", endpoint=endpoint, bucket=bucket, key=key,
            params=None, body=b"", access_key=access_key, secret_key=secret_key,
            region=region, expected_statuses={200}, timeout_seconds=timeout_seconds,
        )
        actual_bytes = int(head.headers.get("Content-Length", "-1"))
        if actual_bytes != required_bytes:
            raise RuntimeError(
                f"capacity probe object size mismatch: expected={required_bytes} actual={actual_bytes}"
            )
        _request(
            client, method="DELETE", endpoint=endpoint, bucket=bucket, key=key,
            params=None, body=b"", access_key=access_key, secret_key=secret_key,
            region=region, expected_statuses={200, 204}, timeout_seconds=timeout_seconds,
        )
        deleted = True
        return {
            "allowed": True,
            "bucket": bucket,
            "required_bytes": required_bytes,
            "part_size": part_size,
            "part_count": part_count,
            "probe_object_deleted": True,
            "elapsed_seconds": round(
                (datetime.now(timezone.utc) - started).total_seconds(), 2
            ),
        }
    finally:
        if upload_id and not completed:
            try:
                _request(
                    client, method="DELETE", endpoint=endpoint, bucket=bucket, key=key,
                    params={"uploadId": upload_id}, body=b"", access_key=access_key,
                    secret_key=secret_key, region=region,
                    expected_statuses={200, 204}, timeout_seconds=timeout_seconds,
                )
            except Exception as exc:
                print(f"S3_CAPACITY_ABORT_WARNING={type(exc).__name__}",
                      file=sys.stderr)
        elif completed and not deleted:
            try:
                _request(
                    client, method="DELETE", endpoint=endpoint, bucket=bucket, key=key,
                    params=None, body=b"", access_key=access_key,
                    secret_key=secret_key, region=region,
                    expected_statuses={200, 204}, timeout_seconds=timeout_seconds,
                )
            except Exception as exc:
                print(f"S3_CAPACITY_DELETE_WARNING={type(exc).__name__}",
                      file=sys.stderr)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--required-bytes", type=int, default=_DEFAULT_REQUIRED_BYTES)
    parser.add_argument("--part-size", type=int, default=_DEFAULT_PART_SIZE)
    parser.add_argument("--timeout-seconds", type=int, default=120)
    args = parser.parse_args()
    try:
        from bankrotai.core import get_settings

        settings = get_settings()
        if not settings.map_object_store_enabled:
            raise RuntimeError("Map object store is disabled")
        result = probe_capacity(
            endpoint=settings.map_object_store_endpoint,
            bucket=settings.map_object_store_bucket,
            access_key=settings.map_object_store_access_key or "",
            secret_key=settings.map_object_store_secret_key or "",
            region=settings.map_object_store_region,
            required_bytes=args.required_bytes,
            part_size=args.part_size,
            timeout_seconds=args.timeout_seconds,
        )
    except Exception as exc:
        result = {
            "allowed": False,
            "error_type": type(exc).__name__,
            "error": str(exc)[:500],
            "required_bytes": args.required_bytes,
        }
        print("S3_CAPACITY_PROBE=" + json.dumps(result, sort_keys=True))
        return 2
    print("S3_CAPACITY_PROBE=" + json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
