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
    def isolated_persist(sess, normalized, *, isolate_source):
        assert isolate_source is True
        return sentinel
    monkeypatch.setattr(module, "persist_lot", isolated_persist)
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



def test_isolated_canary_key_does_not_alias_different_auction_same_cadastral():
    from bankrotai.logic import _canonical_key

    first = SimpleNamespace(
        cadastral_number="76:02:022201:38", source_system="m-ets.ru",
        external_id="240000-1",
    )
    second = SimpleNamespace(
        cadastral_number="76:02:022201:38", source_system="cdtrf.ru",
        external_id="cdtrf:390899",
    )
    # Distinct source publications must never share a key by cadastre alone.
    assert _canonical_key(first) != _canonical_key(second)
    assert _canonical_key(first, isolate_source=True) != _canonical_key(
        second, isolate_source=True
    )


def test_isolated_canary_key_is_idempotent_for_same_source_external_id():
    from bankrotai.logic import _canonical_key

    first = lot()
    again = lot()
    assert _canonical_key(first, isolate_source=True) == _canonical_key(
        again, isolate_source=True
    )
