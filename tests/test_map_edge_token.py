from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

from bankrotai.services.map_edge_token import create_map_edge_token


def _decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def test_map_edge_token_is_signed_scoped_and_short_lived():
    secret = "edge-secret-that-is-long-enough-for-tests"
    before = int(time.time())
    token, expires_at = create_map_edge_token(
        user_id=17,
        dataset_version="dataset-v1",
        secret=secret,
        ttl_seconds=120,
    )
    encoded, signature = token.split(".", 1)
    payload = json.loads(_decode(encoded))
    expected = hmac.new(
        secret.encode("utf-8"),
        encoded.encode("ascii"),
        hashlib.sha256,
    ).digest()

    assert hmac.compare_digest(_decode(signature), expected)
    assert payload["sub"] == 17
    assert payload["v"] == "dataset-v1"
    assert before <= payload["iat"] <= int(time.time())
    assert payload["exp"] == expires_at
    assert 119 <= expires_at - before <= 121


def test_map_edge_token_ttl_is_bounded():
    secret = "edge-secret-that-is-long-enough-for-tests"
    _short, short_exp = create_map_edge_token(
        user_id=1,
        dataset_version="dataset-v1",
        secret=secret,
        ttl_seconds=1,
    )
    _long, long_exp = create_map_edge_token(
        user_id=1,
        dataset_version="dataset-v1",
        secret=secret,
        ttl_seconds=10_000,
    )
    now = int(time.time())

    assert 29 <= short_exp - now <= 30
    assert 599 <= long_exp - now <= 600
