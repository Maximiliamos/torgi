"""Official EFRSB REST Publications API 1.4 client (event evidence, NOT auction lots).

Read-only transport. Never persists, archives, or publishes data. The official
/trade-messages endpoint returns ETP events, not individual sale lots.
Spec: https://fedresurs-demo.interfax.ru/helps/bankrupt/Service_rest_1.4.0.pdf
"""
from __future__ import annotations

import os
import time
from datetime import date, timedelta
from typing import Any
from uuid import UUID

import requests

DEMO_URL = "https://bank-publications-demo.fedresurs.ru"
PRODUCTION_URL = "https://bank-publications-prod.fedresurs.ru"
ALLOWED_URLS = frozenset({DEMO_URL, PRODUCTION_URL})
ALLOWED_KINDS = frozenset({"messages", "trade-messages"})


class FedresursProtocolError(ValueError):
    """A response is incomplete or does not match the official API contract."""


def _day(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("Dates must be YYYY-MM-DD") from exc


def _guid(value: str) -> str:
    try:
        return str(UUID(value))
    except (ValueError, AttributeError) as exc:
        raise ValueError("Message id must be a GUID") from exc


class FedresursPublicationsClient:
    """Limited single-threaded HTTPS access with fail-closed pagination."""

    def __init__(
        self,
        *,
        login: str | None = None,
        password: str | None = None,
        base_url: str | None = None,
        session: requests.Session | None = None,
    ) -> None:
        self.login = login if login is not None else os.getenv("FEDRESURS_LOGIN")
        self.password = password if password is not None else os.getenv("FEDRESURS_PASSWORD")
        base = (base_url or os.getenv("FEDRESURS_BASE_URL") or PRODUCTION_URL).rstrip("/")
        if base not in ALLOWED_URLS:
            raise ValueError("FEDRESURS_BASE_URL must be the official demo or production HTTPS host")
        self.base_url = base
        self.session = session or requests.Session()
        self.timeout = (5, 30)
        self._token: str | None = None
        self._token_expires_at: float = 0.0

    def authenticate(self) -> str:
        if self._token and time.monotonic() < self._token_expires_at:
            return self._token
        if not self.login or not self.password:
            raise RuntimeError("Set FEDRESURS_LOGIN and FEDRESURS_PASSWORD outside of Git")
        # Official v1/auth requires the *original* password inside HTTPS JSON,
        # not the SHA-512 digest used by the old TORGI implementation.
        response = self.session.post(
            f"{self.base_url}/v1/auth",
            json={"login": self.login, "password": self.password},
            timeout=self.timeout,
            allow_redirects=False,
        )
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict) or not isinstance(data.get("jwt"), str) or not data["jwt"]:
            raise FedresursProtocolError("EFRSB auth response missing JWT")
        self._token = data["jwt"]
        self._token_expires_at = time.monotonic() + 7 * 60 * 60  # token is valid 8h
        return self._token

    def _get(self, path: str, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
        response = self.session.get(
            f"{self.base_url}/v1/{path}",
            params=params,
            headers={"Authorization": f"Bearer {self.authenticate()}", "Accept": "application/json"},
            timeout=self.timeout,
            allow_redirects=False,
        )
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise FedresursProtocolError("EFRSB response must be a JSON object")
        if "code" in data and "pageData" not in data:
            raise FedresursProtocolError(f"EFRSB rejected request (code {data['code']})")
        return data

    @staticmethod
    def _validate_kind(kind: str) -> None:
        if kind not in ALLOWED_KINDS:
            raise ValueError(f"Unsupported EFRSB publication collection: {kind}")

    def list_page(
        self,
        *,
        kind: str,
        start_date: str,
        end_date: str,
        offset: int = 0,
        limit: int = 100,
        message_types: str | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        self._validate_kind(kind)
        start, end = _day(start_date), _day(end_date)
        # A half-open range avoids duplicate midnight publications across days.
        exclusive_end = end + timedelta(days=1)
        if end < start or (exclusive_end - start).days > 31:
            raise ValueError("EFRSB publication interval must be within 1-31 calendar days")
        if not 1 <= limit <= 1000 or offset < 0:
            raise ValueError("EFRSB limit must be 1..1000 and offset >= 0")
        params: dict[str, Any] = {
            "datePublishBegin": f"gte:{start.isoformat()}T00:00:00",
            "datePublishEnd": f"lt:{exclusive_end.isoformat()}T00:00:00",
            "limit": limit,
            "offset": offset,
            "sort": "DatePublish:asc",
            "includeContent": "false",
        }
        if message_types:
            if len(message_types) > 250 or not all(
                part.strip().replace("_", "").isalnum()
                for part in message_types.split(",")
            ):
                raise ValueError("Invalid EFRSB message type filter")
            params["type"] = message_types
        payload = self._get(kind, params=params)
        rows, total = payload.get("pageData"), payload.get("total")
        if not isinstance(rows, list) or not isinstance(total, int) or total < 0:
            raise FedresursProtocolError("EFRSB pageData/total missing or invalid")
        if len(rows) > limit or len(rows) > total:
            raise FedresursProtocolError("EFRSB returned inconsistent page length")
        if not all(isinstance(row, dict) and row.get("guid") and row.get("datePublish") for row in rows):
            raise FedresursProtocolError("EFRSB page contains a malformed publication")
        if offset < total and not rows:
            raise FedresursProtocolError("EFRSB returned an empty page before total was reached")
        return rows, total

    def collect_bounded(
        self,
        *,
        kind: str,
        start_date: str,
        end_date: str,
        page_size: int = 100,
        max_pages: int = 2,
        message_types: str | None = None,
    ) -> dict[str, Any]:
        if not 1 <= page_size <= 1000 or not 1 <= max_pages <= 50:
            raise ValueError("Bounded pilot only: page_size <=1000, max_pages <=50")
        offset, total = 0, None
        rows: list[dict[str, Any]] = []
        seen: set[str] = set()
        for _ in range(max_pages):
            page, reported_total = self.list_page(
                kind=kind, start_date=start_date, end_date=end_date,
                offset=offset, limit=page_size, message_types=message_types,
            )
            if total is not None and reported_total != total:
                raise FedresursProtocolError("EFRSB page total changed mid-scan; retry bounded interval")
            total = reported_total
            for row in page:
                guid = _guid(str(row["guid"]))
                if guid in seen:
                    raise FedresursProtocolError("Duplicate EFRSB message across pages")
                seen.add(guid)
                rows.append(row)
            offset += len(page)
            if offset >= total:
                break
            if len(page) < page_size:
                raise FedresursProtocolError("EFRSB short page with remaining records")
        # This is an event index; no conversion to a sale lot is attempted.
        return {
            "kind": kind, "start_date": start_date, "end_date": end_date,
            "reported_total": total, "collected": len(rows),
            "complete": len(rows) == total,
            "events": rows,
        }

    def fetch_message(self, kind: str, guid: str) -> dict[str, Any]:
        self._validate_kind(kind)
        data = self._get(f"{kind}/{_guid(guid)}")
        if str(data.get("guid", "")).lower() != _guid(guid):
            raise FedresursProtocolError("EFRSB detail GUID mismatch")
        return data

    def list_trade_places(self, *, limit: int = 100, offset: int = 0) -> dict[str, Any]:
        if not 1 <= limit <= 1000 or offset < 0:
            raise ValueError("Invalid EFRSB trade-place pagination")
        data = self._get("trade-places", params={"limit": limit, "offset": offset})
        if not isinstance(data.get("pageData"), list) or not isinstance(data.get("total"), int):
            raise FedresursProtocolError("EFRSB trade-places page missing pageData/total")
        return data
