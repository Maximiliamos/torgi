from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def create_map_edge_token(
    *,
    user_id: int,
    dataset_version: str,
    secret: str,
    ttl_seconds: int = 120,
) -> tuple[str, int]:
    if not secret:
        raise ValueError("map edge token secret is not configured")
    now = int(time.time())
    expires_at = now + max(30, min(600, int(ttl_seconds)))
    payload = {
        "sub": int(user_id),
        "v": str(dataset_version),
        "iat": now,
        "exp": expires_at,
    }
    encoded = _b64encode(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    )
    signature = _b64encode(
        hmac.new(
            secret.encode("utf-8"),
            encoded.encode("ascii"),
            hashlib.sha256,
        ).digest()
    )
    return f"{encoded}.{signature}", expires_at
