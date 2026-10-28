"""Local-only API benchmark safety guard regression tests."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/benchmark_local_api.py"
SPEC = importlib.util.spec_from_file_location("benchmark_local_api", SCRIPT)
assert SPEC and SPEC.loader
BENCH = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BENCH)


@pytest.mark.parametrize("url", [
    "https://sterdez.online",
    "http://example.com/api",
    "http://127.0.0.1:18000/health?token=abc",
    "http://user:pass@localhost:18000/health",
])
def test_benchmark_rejects_external_or_secret_urls(url):
    with pytest.raises(ValueError):
        BENCH.local_url(url)


def test_benchmark_preserves_http_errors_as_status(monkeypatch):
    from urllib.error import HTTPError

    def fake_urlopen(endpoint, timeout):
        raise HTTPError(endpoint, 401, "unauthorized", {}, None)

    monkeypatch.setattr(BENCH, "urlopen", fake_urlopen)
    result = BENCH.measure("http://127.0.0.1:18000/health/live", count=3, timeout=1, interval=0)
    assert result["statuses"] == {"401": 3}
    assert result["p95_ms"] >= 0
    assert result["requests"] == 3
