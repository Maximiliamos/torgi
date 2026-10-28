"""Manual canary may not merge another platform's property by cadastre alone."""
from types import SimpleNamespace

import pytest

from bankrotai.services.etp_canary_persistence import (
    CanaryDuplicateReviewRequired, persist_canary_lot,
)


class Session:
    def __init__(self, other):
        self.other = other
    def scalar(self, stmt):
        return self.other


def lot():
    return SimpleNamespace(
        cadastral_number="77:01:0004010:119", source_system="m-ets.ru",
        external_id="243003-1",
    )


def test_cross_source_collision_fails_without_persist(monkeypatch):
    import bankrotai.services.etp_canary_persistence as module
    monkeypatch.setattr(module, "persist_lot", lambda *args: pytest.fail("must not write"))
    with pytest.raises(CanaryDuplicateReviewRequired, match="manual review"):
        persist_canary_lot(Session(123), lot(), "admin")


def test_no_collision_records_audit_and_persists(monkeypatch):
    import bankrotai.services.etp_canary_persistence as module
    actions = []
    sentinel = SimpleNamespace(id=987)
    monkeypatch.setattr(module, "persist_lot", lambda sess, normalized: sentinel)
    monkeypatch.setattr(module, "log_action", lambda *args: actions.append(args))
    assert persist_canary_lot(Session(None), lot(), "admin") is sentinel
    assert actions and actions[0][1] == "admin"
    assert actions[0][2] == "import_verified_etp_canary"


def test_missing_cadastre_never_written(monkeypatch):
    import bankrotai.services.etp_canary_persistence as module
    obj = lot()
    obj.cadastral_number = None
    monkeypatch.setattr(module, "persist_lot", lambda *args: pytest.fail("must not write"))
    with pytest.raises(ValueError, match="cadastral"):
        persist_canary_lot(Session(None), obj, "admin")
