"""Fail-closed, bounded read-only retry for a transient AUSIB self-redirect.

At 2026-11-04 Home saw AUSIB HTTP 302 Location == request URL, then a
separate read-only check returned HTTP 200 HTML with lot links. A single
same-URL retry is allowed; no cross-origin redirect is ever followed.
"""
from __future__ import annotations

import time
from urllib.parse import urljoin

import requests


def get_catalogue_response(
    session: requests.Session,
    *,
    source_id: str,
    url: str,
    timeout: float,
) -> requests.Response:
    response = session.get(url, timeout=timeout, allow_redirects=False)
    if source_id != "ausib.ru" or response.status_code != 302:
        return response
    location = (getattr(response, "headers", None) or {}).get("Location")
    if (
        location
        and response.url == url
        and urljoin(response.url, location) == url
    ):
        # Retry the same previously validated HTTPS URL once. If the server
        # still responds 302 (or refuses the request), the caller raises;
        # never loop, follow a redirect or classify the source as empty.
        time.sleep(1.0)
        return session.get(url, timeout=timeout, allow_redirects=False)
    return response
