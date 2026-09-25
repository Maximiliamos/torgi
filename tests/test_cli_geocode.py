import json
import sys

import bankrotai.cli as cli
from bankrotai.services import geo_backfill


def test_geocode_pending_cli_reports_busy_lock_without_failure(monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli, "init_db", lambda: None)

    def busy(*_args, **_kwargs):
        raise geo_backfill.GeoBatchAlreadyRunning("Another geocoding batch is already running")

    monkeypatch.setattr(geo_backfill, "geocode_pending_lots", busy)
    monkeypatch.setattr(
        sys,
        "argv",
        ["bankrotai", "geocode-pending", "--limit", "10"],
    )

    cli.main()

    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "status": "busy",
        "reason": "another-geocoding-batch-is-running",
    }
