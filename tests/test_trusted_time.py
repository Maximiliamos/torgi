from datetime import datetime, timezone

from bankrotai.services import trusted_time


def test_trusted_time_applies_verified_offset(monkeypatch) -> None:
    monkeypatch.setattr(trusted_time, "_probe", lambda: (2.0, "https://time100.ru/"))
    monkeypatch.setattr(trusted_time, "_checked_monotonic", 0.0)
    before = datetime.now(timezone.utc)
    result = trusted_time.trusted_utc_now(refresh=True)
    after = datetime.now(timezone.utc)
    assert before.timestamp() + 1.9 <= result.timestamp() <= after.timestamp() + 2.1
    assert trusted_time.trusted_time_status()["synchronized"] is True


def test_trusted_time_falls_back_to_system_clock(monkeypatch) -> None:
    def fail():
        raise RuntimeError("offline")

    monkeypatch.setattr(trusted_time, "_probe", fail)
    before = datetime.now(timezone.utc)
    result = trusted_time.trusted_utc_now(refresh=True)
    after = datetime.now(timezone.utc)
    assert before <= result <= after
    assert trusted_time.trusted_time_status()["source"] == "system_utc"
