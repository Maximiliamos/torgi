from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from bankrotai.scraper_contracts import TBankrotSearchFilters
from bankrotai.scrapers import TBankrotClient


TBANKROT_HOME_URL = "https://tbankrot.ru/"
TBANKROT_PROBE_URL = "https://tbankrot.ru/?p=search&parent_cat=2&sub_cat=3%2C4%2C5"
TBANKROT_VIEWPORT = {"width": 1280, "height": 760}
_AUTH_IDLE_SECONDS = 20 * 60
_STATUS_CACHE_SECONDS = 45


@dataclass(slots=True)
class TBankrotAuthState:
    state: str
    browser_active: bool
    authenticated: bool
    requires_auth: bool
    message: str
    checked_at: str
    page_url: str | None = None
    page_title: str | None = None
    saved_at: str | None = None
    cookie_count: int = 0
    source_total: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "browser_active": self.browser_active,
            "authenticated": self.authenticated,
            "requires_auth": self.requires_auth,
            "message": self.message,
            "checked_at": self.checked_at,
            "page_url": self.page_url,
            "page_title": self.page_title,
            "saved_at": self.saved_at,
            "cookie_count": self.cookie_count,
            "source_total": self.source_total,
            "viewport": TBANKROT_VIEWPORT,
        }


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _cookie_file_path() -> Path:
    configured = os.getenv("TBANKROT_COOKIE_FILE", "").strip()
    if configured:
        return Path(configured)
    return Path("/run/tbankrot-auth/cookies.json")


def _read_cookie_metadata(path: Path) -> tuple[str | None, int]:
    if not path.exists():
        return None, 0
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, 0
    cookies = payload.get("cookies", []) if isinstance(payload, dict) else []
    captured_at = payload.get("capturedAt") if isinstance(payload, dict) else None
    return (str(captured_at) if captured_at else None, len(cookies) if isinstance(cookies, list) else 0)


def probe_saved_tbankrot_session(path: Path | None = None) -> TBankrotAuthState:
    cookie_path = path or _cookie_file_path()
    saved_at, cookie_count = _read_cookie_metadata(cookie_path)
    if not cookie_path.exists() or cookie_count == 0:
        return TBankrotAuthState(
            state="requires_auth",
            browser_active=False,
            authenticated=False,
            requires_auth=True,
            message="Нужна авторизация TBankrot",
            checked_at=_utc_iso(),
            saved_at=saved_at,
            cookie_count=cookie_count,
        )
    try:
        client = TBankrotClient(cookie_file=str(cookie_path))
        _lots, meta = client.search_filtered_lots(
            TBankrotSearchFilters(
                category_codes=TBankrotClient.REAL_ESTATE_CATEGORY_CODES,
                page=1,
                page_size=20,
            )
        )
        return TBankrotAuthState(
            state="authenticated",
            browser_active=False,
            authenticated=True,
            requires_auth=False,
            message="Сессия TBankrot активна",
            checked_at=_utc_iso(),
            saved_at=saved_at,
            cookie_count=cookie_count,
            source_total=int(meta["total"]) if isinstance(meta.get("total"), int) else None,
        )
    except Exception as exc:
        message = str(exc)
        requires_auth = "access_limited" in message or "session cookie" in message.lower()
        return TBankrotAuthState(
            state="requires_auth" if requires_auth else "unavailable",
            browser_active=False,
            authenticated=False,
            requires_auth=requires_auth,
            message="Сессия TBankrot истекла — войдите снова" if requires_auth else "TBankrot временно недоступен",
            checked_at=_utc_iso(),
            saved_at=saved_at,
            cookie_count=cookie_count,
        )


def _allowed_auth_host(host: str | None) -> bool:
    if not host:
        return False
    host = host.casefold().strip(".")
    allowed_suffixes = (
        "tbankrot.ru",
        "yandex.ru",
        "yandex.com",
        "yandexcloud.net",
        "google.com",
        "gstatic.com",
        "recaptcha.net",
        "hcaptcha.com",
        "cloudflare.com",
        "cloudflareinsights.com",
    )
    return any(host == suffix or host.endswith("." + suffix) for suffix in allowed_suffixes)


class TBankrotAuthBrowser:
    """One admin-controlled browser session dedicated to TBankrot authentication.

    Passwords and CAPTCHA answers are never persisted by this service. Only the
    resulting TBankrot cookies are written to the protected runtime directory.
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._playwright: Any = None
        self._browser: Any = None
        self._context: Any = None
        self._page: Any = None
        self._last_used = 0.0
        self._last_probe_at = 0.0
        self._last_probe: TBankrotAuthState | None = None

    async def _route(self, route: Any) -> None:
        parsed = urlparse(route.request.url)
        if parsed.scheme not in {"http", "https"} or not _allowed_auth_host(parsed.hostname):
            await route.abort()
            return
        await route.continue_()

    async def _ensure_browser(self) -> None:
        if self._page is not None:
            self._last_used = time.monotonic()
            return
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise RuntimeError("TBankrot browser runtime is not installed") from exc
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-background-networking",
                "--disable-default-apps",
            ],
        )
        self._context = await self._browser.new_context(
            viewport=TBANKROT_VIEWPORT,
            locale="ru-RU",
            timezone_id="Europe/Moscow",
            accept_downloads=False,
        )
        await self._context.route("**/*", self._route)
        cookie_path = _cookie_file_path()
        if cookie_path.exists():
            try:
                payload = json.loads(cookie_path.read_text(encoding="utf-8"))
                cookies = payload.get("cookies", payload) if isinstance(payload, dict) else payload
                if isinstance(cookies, list):
                    safe = [
                        cookie
                        for cookie in cookies
                        if isinstance(cookie, dict)
                        and str(cookie.get("domain") or "").lstrip(".").endswith("tbankrot.ru")
                    ]
                    if safe:
                        await self._context.add_cookies(safe)
            except (OSError, ValueError):
                pass
        self._page = await self._context.new_page()
        self._last_used = time.monotonic()

    async def _browser_state(self, *, message: str = "Окно авторизации открыто") -> TBankrotAuthState:
        saved_at, cookie_count = _read_cookie_metadata(_cookie_file_path())
        url = self._page.url if self._page is not None else None
        title = None
        if self._page is not None:
            try:
                title = await self._page.title()
            except Exception:
                title = None
        return TBankrotAuthState(
            state="browser_active",
            browser_active=self._page is not None,
            authenticated=False,
            requires_auth=True,
            message=message,
            checked_at=_utc_iso(),
            page_url=url,
            page_title=title,
            saved_at=saved_at,
            cookie_count=cookie_count,
        )

    async def start(self) -> dict[str, Any]:
        async with self._lock:
            await self._ensure_browser()
            await self._page.goto(TBANKROT_HOME_URL, wait_until="domcontentloaded", timeout=30_000)
            self._last_used = time.monotonic()
            return (await self._browser_state()).as_dict()

    async def status(self, *, force_probe: bool = False) -> dict[str, Any]:
        async with self._lock:
            if self._page is not None and time.monotonic() - self._last_used > _AUTH_IDLE_SECONDS:
                await self._close_unlocked()
            if self._page is not None:
                return (await self._browser_state()).as_dict()
            now = time.monotonic()
            if not force_probe and self._last_probe is not None and now - self._last_probe_at < _STATUS_CACHE_SECONDS:
                return self._last_probe.as_dict()
        state = await asyncio.to_thread(probe_saved_tbankrot_session)
        async with self._lock:
            self._last_probe = state
            self._last_probe_at = time.monotonic()
        return state.as_dict()

    async def screenshot(self) -> bytes:
        async with self._lock:
            await self._ensure_browser()
            self._last_used = time.monotonic()
            return await self._page.screenshot(type="jpeg", quality=78, full_page=False)

    async def click(self, x: float, y: float) -> dict[str, Any]:
        async with self._lock:
            await self._ensure_browser()
            x = max(0.0, min(float(TBANKROT_VIEWPORT["width"]), float(x)))
            y = max(0.0, min(float(TBANKROT_VIEWPORT["height"]), float(y)))
            await self._page.mouse.click(x, y)
            self._last_used = time.monotonic()
            return {"status": "ok"}

    async def type_text(self, text: str) -> dict[str, Any]:
        if len(text) > 512:
            raise ValueError("Input chunk is too large")
        async with self._lock:
            await self._ensure_browser()
            await self._page.keyboard.insert_text(text)
            self._last_used = time.monotonic()
            return {"status": "ok"}

    async def press(self, key: str) -> dict[str, Any]:
        allowed = {
            "Enter", "Tab", "Escape", "Backspace", "Delete",
            "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight",
            "Home", "End", "PageUp", "PageDown", "Space",
        }
        if key not in allowed:
            raise ValueError("Unsupported browser key")
        async with self._lock:
            await self._ensure_browser()
            await self._page.keyboard.press(key)
            self._last_used = time.monotonic()
            return {"status": "ok"}

    async def scroll(self, delta_y: float) -> dict[str, Any]:
        async with self._lock:
            await self._ensure_browser()
            await self._page.mouse.wheel(0, max(-2000.0, min(2000.0, float(delta_y))))
            self._last_used = time.monotonic()
            return {"status": "ok"}

    async def verify_and_save(self) -> dict[str, Any]:
        async with self._lock:
            await self._ensure_browser()
            await self._page.goto(TBANKROT_PROBE_URL, wait_until="domcontentloaded", timeout=30_000)
            html = await self._page.content()
            if TBankrotClient._is_listing_access_limited(html):
                return (await self._browser_state(message="Авторизация ещё не подтверждена TBankrot")).as_dict()
            total = TBankrotClient._extract_search_total(html)
            cookies = await self._context.cookies([TBANKROT_HOME_URL])
            cookies = [
                cookie
                for cookie in cookies
                if str(cookie.get("domain") or "").lstrip(".").endswith("tbankrot.ru")
            ]
            if not cookies:
                return (await self._browser_state(message="TBankrot не выдал авторизационные cookies")).as_dict()
            cookie_path = _cookie_file_path()
            cookie_path.parent.mkdir(parents=True, exist_ok=True)
            captured_at = _utc_iso()
            temp_path = cookie_path.with_suffix(cookie_path.suffix + ".tmp")
            temp_path.write_text(
                json.dumps({"version": 2, "capturedAt": captured_at, "cookies": cookies}, ensure_ascii=False),
                encoding="utf-8",
            )
            try:
                os.chmod(temp_path, 0o600)
            except OSError:
                pass
            temp_path.replace(cookie_path)
            self._last_probe = TBankrotAuthState(
                state="authenticated",
                browser_active=True,
                authenticated=True,
                requires_auth=False,
                message="Авторизация TBankrot подтверждена",
                checked_at=_utc_iso(),
                page_url=self._page.url,
                page_title=await self._page.title(),
                saved_at=captured_at,
                cookie_count=len(cookies),
                source_total=total,
            )
            self._last_probe_at = time.monotonic()
            return self._last_probe.as_dict()

    async def _close_unlocked(self) -> None:
        for item, method in (
            (self._context, "close"),
            (self._browser, "close"),
            (self._playwright, "stop"),
        ):
            if item is not None:
                try:
                    await getattr(item, method)()
                except Exception:
                    pass
        self._page = None
        self._context = None
        self._browser = None
        self._playwright = None

    async def close(self) -> dict[str, str]:
        async with self._lock:
            await self._close_unlocked()
            return {"status": "closed"}


tbankrot_auth_browser = TBankrotAuthBrowser()
