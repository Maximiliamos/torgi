from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path


def _load_audit_module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "audit_source_connectivity.py"
    spec = importlib.util.spec_from_file_location("audit_source_connectivity", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


audit = _load_audit_module()


def test_connector_init_failure_is_reported_per_source(monkeypatch):
    def broken_create(_source_id):
        raise RuntimeError("missing runtime credential")

    monkeypatch.setattr(audit.connector_registry, "create", broken_create)

    result = asyncio.run(
        audit.audit_source("tbankrot.ru", object(), timeout=5.0)
    )

    assert result["source"] == "tbankrot.ru"
    assert result["status"] == "failed"
    assert result["failure_stage"] == "connector_init"
    assert result["error_type"] == "RuntimeError"
    assert "missing runtime credential" in result["error"]


def test_search_failure_is_distinguished_from_constructor_failure(monkeypatch):
    class Connector:
        async def search(self, _filters):
            raise TimeoutError("provider timed out")

    monkeypatch.setattr(
        audit.connector_registry,
        "create",
        lambda _source_id: Connector(),
    )

    result = asyncio.run(
        audit.audit_source("torgi.gov.ru", object(), timeout=5.0)
    )

    assert result["source"] == "torgi.gov.ru"
    assert result["status"] == "failed"
    assert result["failure_stage"] == "search"
    assert result["error_type"] == "TimeoutError"
