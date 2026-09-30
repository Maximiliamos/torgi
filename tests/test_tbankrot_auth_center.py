from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from bankrotai import api
from bankrotai.auth import AuthenticatedUser
from bankrotai.services import tbankrot_auth


def test_missing_cookie_file_requires_auth(tmp_path, monkeypatch) -> None:
    cookie_file = tmp_path / "cookies.json"
    monkeypatch.setenv("TBANKROT_COOKIE_FILE", str(cookie_file))

    state = tbankrot_auth.probe_saved_tbankrot_session()

    assert state.state == "requires_auth"
    assert state.requires_auth is True
    assert state.authenticated is False
    assert state.cookie_count == 0


def test_auth_browser_allowlist_is_bounded() -> None:
    assert tbankrot_auth._allowed_auth_host("tbankrot.ru")
    assert tbankrot_auth._allowed_auth_host("www.tbankrot.ru")
    assert tbankrot_auth._allowed_auth_host("smartcaptcha.yandexcloud.net")
    assert not tbankrot_auth._allowed_auth_host("127.0.0.1")
    assert not tbankrot_auth._allowed_auth_host("localhost")
    assert not tbankrot_auth._allowed_auth_host("evil.example")


def test_saved_session_probe_never_returns_cookie_values(tmp_path, monkeypatch) -> None:
    cookie_file = tmp_path / "cookies.json"
    cookie_file.write_text(json.dumps({
        "capturedAt": "2026-09-30T01:00:00+00:00",
        "cookies": [{"name": "session", "value": "top-secret", "domain": ".tbankrot.ru", "path": "/"}],
    }), encoding="utf-8")
    monkeypatch.setenv("TBANKROT_COOKIE_FILE", str(cookie_file))

    class FakeClient:
        REAL_ESTATE_CATEGORY_CODES = "3,4,5"

        def __init__(self, *, cookie_file: str):
            assert cookie_file == str(cookie_file_path)

        def search_filtered_lots(self, _filters):
            return [], {"total": 123}

    cookie_file_path = cookie_file
    monkeypatch.setattr(tbankrot_auth, "TBankrotClient", FakeClient)

    payload = tbankrot_auth.probe_saved_tbankrot_session().as_dict()

    assert payload["authenticated"] is True
    assert payload["source_total"] == 123
    assert payload["cookie_count"] == 1
    assert "top-secret" not in json.dumps(payload)


def test_auth_center_api_requires_admin_and_starts_targeted_sync(monkeypatch) -> None:
    client = TestClient(api.app)
    monkeypatch.setattr(api.settings, "api_read_only", True)

    api.app.dependency_overrides[api.require_admin] = lambda: AuthenticatedUser(
        id=1, username="admin", role="admin"
    )

    async def fake_status(*, force_probe: bool = False):
        return {
            "state": "authenticated",
            "browser_active": False,
            "authenticated": True,
            "requires_auth": False,
            "message": "ok",
            "checked_at": "2026-09-30T01:00:00+00:00",
            "cookie_count": 2,
            "viewport": {"width": 1280, "height": 760},
        }

    monkeypatch.setattr(tbankrot_auth.tbankrot_auth_browser, "status", fake_status)
    monkeypatch.setattr(
        api,
        "schedule_nationwide_lot_sync",
        lambda **kwargs: "tbankrot-run" if kwargs["mode"] == "source:tbankrot.ru" else "wrong",
    )
    try:
        response = client.post("/api/tbankrot/sync")
    finally:
        api.app.dependency_overrides.pop(api.require_admin, None)

    assert response.status_code == 202
    assert response.json() == {"task_id": "tbankrot-run", "status": "queued"}


def test_verify_auth_saves_only_tbankrot_cookies_and_then_queues_sync(tmp_path, monkeypatch) -> None:
    cookie_file = tmp_path / "cookies.json"
    monkeypatch.setenv("TBANKROT_COOKIE_FILE", str(cookie_file))
    manager = tbankrot_auth.TBankrotAuthBrowser()

    class FakePage:
        url = tbankrot_auth.TBANKROT_PROBE_URL

        async def goto(self, *_args, **_kwargs):
            return None

        async def content(self):
            return "<html><div class='search_result_col'><b class='default'>321</b></div></html>"

        async def title(self):
            return "TBankrot"

    class FakeContext:
        async def cookies(self, _urls):
            return [
                {"name": "session", "value": "secret", "domain": ".tbankrot.ru", "path": "/"},
                {"name": "foreign", "value": "ignore", "domain": ".example.com", "path": "/"},
            ]

    manager._page = FakePage()
    manager._context = FakeContext()

    async def run():
        return await manager.verify_and_save()

    import asyncio
    payload = asyncio.run(run())

    assert payload["authenticated"] is True
    saved = json.loads(cookie_file.read_text(encoding="utf-8"))
    assert [item["name"] for item in saved["cookies"]] == ["session"]
    assert saved["cookies"][0]["value"] == "secret"
