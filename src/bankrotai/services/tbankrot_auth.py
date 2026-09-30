from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from bankrotai.core import AppSettings


class TBankrotAuthBrokerError(RuntimeError):
    pass


@dataclass(frozen=True)
class TBankrotFrame:
    content: bytes
    content_type: str


def _configuration(settings: AppSettings) -> tuple[str, str]:
    url = (settings.tbankrot_auth_broker_url or "").rstrip("/")
    token = settings.tbankrot_auth_broker_token or ""
    if not url or not token:
        raise TBankrotAuthBrokerError("TBankrot auth broker is not configured")
    if len(token) < 32:
        raise TBankrotAuthBrokerError("TBankrot auth broker token is invalid")
    return url, token


async def _request(
    settings: AppSettings,
    method: str,
    path: str,
    *,
    json: dict[str, Any] | None = None,
    timeout: float = 15.0,
) -> httpx.Response:
    url, token = _configuration(settings)
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.request(
                method,
                f"{url}{path}",
                json=json,
                headers={"Authorization": f"Bearer {token}"},
            )
    except httpx.HTTPError as exc:
        raise TBankrotAuthBrokerError("TBankrot auth broker is unavailable") from exc
    if response.status_code >= 400:
        detail = response.text[:500]
        try:
            payload = response.json()
            detail = str(payload.get("detail") or detail)
        except ValueError:
            pass
        raise TBankrotAuthBrokerError(detail or f"TBankrot auth broker HTTP {response.status_code}")
    return response


async def broker_status(settings: AppSettings) -> dict[str, Any]:
    response = await _request(settings, "GET", "/status", timeout=5.0)
    return dict(response.json())


async def start_browser_session(settings: AppSettings) -> dict[str, Any]:
    response = await _request(settings, "POST", "/session/start", timeout=40.0)
    return dict(response.json())


async def browser_frame(settings: AppSettings, session_id: str) -> TBankrotFrame:
    response = await _request(settings, "GET", f"/session/{session_id}/frame", timeout=15.0)
    return TBankrotFrame(
        content=response.content,
        content_type=response.headers.get("content-type", "image/jpeg"),
    )


async def browser_action(
    settings: AppSettings,
    session_id: str,
    action: dict[str, Any],
) -> dict[str, Any]:
    response = await _request(
        settings,
        "POST",
        f"/session/{session_id}/action",
        json=action,
        timeout=35.0,
    )
    return dict(response.json())


async def verify_browser_session(settings: AppSettings, session_id: str) -> dict[str, Any]:
    response = await _request(
        settings,
        "POST",
        f"/session/{session_id}/verify",
        timeout=45.0,
    )
    return dict(response.json())


async def close_browser_session(settings: AppSettings, session_id: str) -> dict[str, Any]:
    response = await _request(
        settings,
        "POST",
        f"/session/{session_id}/close",
        timeout=15.0,
    )
    return dict(response.json())
