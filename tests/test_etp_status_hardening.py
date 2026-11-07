from bankrotai.connectors.etp_sources.status_normalization import _status


def test_future_application_window_is_scheduled_not_active() -> None:
    assert _status("Ожидается начало приема заявок") == "scheduled"
    assert _status("Ожидается начало приёма заявок") == "scheduled"


def test_completed_application_window_is_not_inferred_active() -> None:
    assert _status("Приём заявок завершён") == "unknown"
    assert _status("Завершен прием заявок") == "unknown"
