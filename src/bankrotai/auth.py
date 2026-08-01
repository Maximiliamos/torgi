from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from bankrotai.core import utc_now
from bankrotai.db import AppUser


_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1


@dataclass(frozen=True, slots=True)
class AuthenticatedUser:
    id: int
    username: str
    role: str


def hash_password(password: str) -> str:
    if len(password) < 12:
        raise ValueError("Password must contain at least 12 characters")
    salt = secrets.token_bytes(16)
    derived = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=32
    )
    return "scrypt${}${}${}${}${}".format(
        _SCRYPT_N,
        _SCRYPT_R,
        _SCRYPT_P,
        _b64encode(salt),
        _b64encode(derived),
    )


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, n, r, p, salt, expected = encoded.split("$", 5)
        if algorithm != "scrypt":
            return False
        derived = hashlib.scrypt(
            password.encode("utf-8"),
            salt=_b64decode(salt),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(_b64decode(expected)),
        )
        return hmac.compare_digest(derived, _b64decode(expected))
    except (ValueError, TypeError):
        return False


def upsert_user(session: Session, username: str, password: str, *, role: str = "reader") -> AppUser:
    normalized = username.strip().casefold()
    if not normalized or len(normalized) > 100:
        raise ValueError("Username must contain 1-100 characters")
    if role not in {"reader", "admin"}:
        raise ValueError("Role must be reader or admin")
    user = session.scalar(select(AppUser).where(func.lower(AppUser.username) == normalized))
    password_hash = hash_password(password)
    if user is None:
        user = AppUser(username=normalized, password_hash=password_hash, role=role)
        session.add(user)
    else:
        user.password_hash = password_hash
        user.role = role
        user.is_active = True
        user.token_version += 1
    session.flush()
    return user


def authenticate_user(session: Session, username: str, password: str) -> AppUser | None:
    normalized = username.strip().casefold()
    user = session.scalar(select(AppUser).where(func.lower(AppUser.username) == normalized))
    valid_hash = user.password_hash if user is not None else hash_password("invalid-password-placeholder")
    valid = verify_password(password, valid_hash)
    if user is None or not valid or not user.is_active:
        return None
    user.last_login_at = utc_now()
    session.flush()
    return user


def create_session_token(user: AppUser, secret: str, *, ttl_seconds: int = 28_800) -> str:
    now = int(time.time())
    payload = {
        "sub": user.id,
        "usr": user.username,
        "role": user.role,
        "ver": user.token_version,
        "iat": now,
        "exp": now + max(300, ttl_seconds),
    }
    encoded = _b64encode(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    signature = _b64encode(hmac.new(secret.encode("utf-8"), encoded.encode("ascii"), hashlib.sha256).digest())
    return f"{encoded}.{signature}"


def verify_session_token(session: Session, token: str, secret: str) -> AuthenticatedUser | None:
    try:
        encoded, signature = token.split(".", 1)
        expected = hmac.new(secret.encode("utf-8"), encoded.encode("ascii"), hashlib.sha256).digest()
        if not hmac.compare_digest(_b64decode(signature), expected):
            return None
        payload = json.loads(_b64decode(encoded))
        if int(payload["exp"]) < int(time.time()):
            return None
        user = session.get(AppUser, int(payload["sub"]))
        if user is None or not user.is_active or user.token_version != int(payload["ver"]):
            return None
        return AuthenticatedUser(id=user.id, username=user.username, role=user.role)
    except (ValueError, KeyError, TypeError, json.JSONDecodeError):
        return None


def session_secret_from_environment() -> str:
    return os.getenv("AUTH_SESSION_SECRET", "")


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
