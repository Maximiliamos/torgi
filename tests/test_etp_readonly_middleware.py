"""ETP public read-only routing gate: preview allowed, mutation forbidden."""
from types import SimpleNamespace

from bankrotai.api import _is_read_only_mvp_path


def _allowed(method, path):
    return _is_read_only_mvp_path(
        SimpleNamespace(method=method, url=SimpleNamespace(path=path))
    )


def test_etp_admin_previews_are_eligible_for_readonly_gateway():
    for path in (
        "/api/etp/pilots/sources",
        "/api/etp/pilots/m-ets.ru/preview",
        "/api/etp/pilots/cdtrf.ru/preview",
        "/api/etp/pilots/nistp.ru/preview",
    ):
        assert _allowed("GET", path)


def test_etp_imports_remain_forbidden_for_readonly_gateway():
    for path in (
        "/api/etp/pilots/m-ets.ru/import/243003-1",
        "/api/etp/pilots/cdtrf.ru/import/390364/390899",
    ):
        assert not _allowed("POST", path)


def test_etp_preview_gateway_does_not_accept_post_or_unrelated_reads():
    assert not _allowed("POST", "/api/etp/pilots/cdtrf.ru/preview")
    assert not _allowed("GET", "/api/etp/pilots/unsafe")
