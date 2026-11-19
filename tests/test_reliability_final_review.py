from types import SimpleNamespace
from contextlib import nullcontext
from threading import Event
import time
import os

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from bankrotai.db import Base, MapDataset, utc_now
from bankrotai.services import reliability as service


def test_promoted_map_invalidates_cached_reconciliation(monkeypatch):
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(engine)
    monkeypatch.setattr(service, '_HEAVY_CACHE', {'at': 0.0})
    monkeypatch.setattr(service, '_cached_public_manifest', lambda dataset: {'checked': False})
    calls = []
    monkeypatch.setattr(service, 'operational_quality_report', lambda *a, **kw: {'integrity': {}})

    def report(session, **kwargs):
        current = session.query(MapDataset).filter_by(is_current=True).one()
        calls.append(current.version)
        return {'dataset': {'version': current.version}}

    monkeypatch.setattr(service, 'map_delivery_reconciliation_report', report)
    with Session(engine) as session:
        old = MapDataset(version='old-r11', is_current=True, status='ready')
        session.add(old)
        session.commit()
        assert service._heavy_quality_bundle(session)[1]['dataset']['version'] == 'old-r11'
        service._heavy_quality_bundle(session)
        old.is_current = False
        session.flush()
        session.add(MapDataset(version='new-r11', is_current=True, status='ready'))
        session.commit()
        latest = service._heavy_quality_bundle(session)[1]
        assert latest['dataset']['version'] == 'new-r11'
        assert latest['checked_at']
    assert calls == ['old-r11', 'new-r11']
    engine.dispose()


@pytest.mark.parametrize(('checked', 'ok', 'expected'), [(True, False, 'incident'), (False, None, 'degraded'), (True, True, 'ok')])
def test_public_delivery_failure_is_an_incident_and_pending_check_is_not(monkeypatch, checked, ok, expected):
    monkeypatch.setattr(service, 'reliability_sources', lambda s: {
        'generated_at': utc_now().isoformat(), 'failed': 0, 'degraded': 0,
        'paused_total': 0, 'not_checked': 0, 'items': [],
    })
    monkeypatch.setattr(service, 'geocoding_progress', lambda s: {'paused': False})
    monkeypatch.setattr(service, 'operations_host_status', lambda s: {
        'backup_healthy': True, 'runner_healthy': True, 'disk': {'free_gb': 50},
    })
    monkeypatch.setattr(service, '_current_quality', lambda s: {'population': 0})
    monkeypatch.setattr(service, 'get_settings', lambda: SimpleNamespace(api_read_only=True))
    monkeypatch.setattr(service, '_heavy_quality_bundle', lambda s: (
        {'integrity': {'current_dataset_count': 1}},
        {'ok': True, 'dataset': {'status': 'ready'}, 'manifest': {'checked': checked, 'ok': ok}},
    ))
    result = service._reliability_summary_uncached(None)
    assert result['system']['status'] == expected
    assert result['map']['ok'] == (checked and ok is True)


@pytest.mark.parametrize(('message', 'secrets'), [
    ('Authorization: Bearer abcXYZ123', ['abcXYZ123']),
    ('Authorization: Basic dXNlcjpwYXNz', ['dXNlcjpwYXNz']),
    ('Cookie: session=secret1; auth=secret2', ['secret1', 'secret2']),
    ('Failed https://operator:pass123@example.test/endpoint', ['operator', 'pass123']),
    ('Payload: {"password": "words with spaces"}', ['words with spaces']),
])
def test_diagnostic_redaction_covers_credentials_with_prefixes_and_spaces(message, secrets):
    safe = service._safe_error(message)
    assert safe and all(secret not in safe for secret in secrets)


def test_cold_and_expired_quality_checks_do_not_block_the_http_request(monkeypatch):
    import bankrotai.db as db

    started, release, finished = Event(), Event(), Event()
    monkeypatch.setattr(service, '_HEAVY_CACHE', {'at': 0.0})
    monkeypatch.setattr(service, '_summary_cache_enabled', lambda session: True)
    monkeypatch.setattr(service, '_dataset_cache_identity', lambda session: (
        ('v1',), {'version': 'v1', 'status': 'ready'}, 1,
    ))
    monkeypatch.setattr(service, 'prepare_reliability_snapshot', lambda session: None)
    monkeypatch.setattr(db, 'SessionLocal', lambda: nullcontext(object()))
    monkeypatch.setattr(service, '_cached_public_manifest', lambda dataset: {'checked': True, 'ok': True})

    def compute(session):
        started.set()
        assert release.wait(3)
        finished.set()
        return {'integrity': {'current_dataset_count': 1}}, {'ok': True, 'dataset': {'version': 'v1'}, 'checked_at': 'captured'}

    monkeypatch.setattr(service, '_compute_quality_bundle', compute)
    begun = time.monotonic()
    _, pending = service._heavy_quality_bundle(object())
    assert time.monotonic()-begun < 0.5
    assert pending['verification_pending'] and pending['manifest']['checked'] is False
    assert not pending['ok'] and started.wait(1)
    try:
        # Concurrent UI polls share one running computation.
        assert service._heavy_quality_bundle(object())[1]['verification_pending']
    finally:
        release.set()
        assert finished.wait(2)
        for _ in range(100):
            with service._HEAVY_CACHE_LOCK:
                running = service._HEAVY_CACHE.get('running')
            if not running:
                break
            time.sleep(0.01)
    _, fresh = service._heavy_quality_bundle(object())
    assert fresh['ok'] and not fresh['verification_pending']
    assert fresh['checked_at'] == 'captured'


def test_pending_integrity_is_explicit_and_does_not_claim_a_healthy_system(monkeypatch):
    monkeypatch.setattr(service, 'reliability_sources', lambda s: {
        'generated_at': utc_now().isoformat(), 'failed': 0, 'degraded': 0,
        'paused_total': 0, 'not_checked': 0, 'items': [],
    })
    monkeypatch.setattr(service, 'geocoding_progress', lambda s: {'paused': False})
    monkeypatch.setattr(service, 'operations_host_status', lambda s: {
        'backup_healthy': True, 'runner_healthy': True, 'disk': {'free_gb': 50},
    })
    monkeypatch.setattr(service, '_current_quality', lambda s: {'population': 0})
    monkeypatch.setattr(service, 'get_settings', lambda: SimpleNamespace(api_read_only=True))
    monkeypatch.setattr(service, '_heavy_quality_bundle', lambda s: (
        {'integrity': {'current_dataset_count': 1}},
        {'ok': False, 'dataset': {'status': 'ready'}, 'verification_pending': True,
         'verification_stale': False, 'manifest': {'checked': False, 'ok': None}},
    ))
    result = service._reliability_summary_uncached(None)
    assert result['system']['status'] == 'degraded'
    assert result['integrity']['checked'] is False
    assert result['integrity']['status'] == 'not_checked'
    assert result['map']['ok'] is False


def test_expired_summary_is_served_immediately_without_a_silent_green_status(monkeypatch):
    import bankrotai.db as db

    started, release = Event(), Event()
    calls = []
    monkeypatch.setattr(service, '_SUMMARY_CACHE', {'at': 0.0, 'snapshot': None})
    monkeypatch.setattr(service, '_summary_cache_enabled', lambda session: True)
    monkeypatch.setattr(service, 'prepare_reliability_snapshot', lambda session: None)
    monkeypatch.setattr(db, 'SessionLocal', lambda: nullcontext(object()))

    def compute(session):
        calls.append(1)
        if len(calls) > 1:
            started.set()
            assert release.wait(3)
        return {'generated_at': str(len(calls)), 'system': {'status': 'ok', 'reasons': []}}

    monkeypatch.setattr(service, '_reliability_summary_uncached', compute)
    assert service.reliability_summary(object())['generated_at'] == '1'
    service._SUMMARY_CACHE['at'] = 0.0
    begun = time.monotonic()
    previous = service.reliability_summary(object())
    assert time.monotonic()-begun < 0.5
    assert previous['generated_at'] == '1' and previous['refresh_state'] == 'refreshing'
    assert previous['system']['status'] == 'degraded'
    assert service._SUMMARY_CACHE['snapshot']['system']['status'] == 'ok'
    try:
        assert started.wait(1)
        assert service.reliability_summary(object())['generated_at'] == '1'
        assert len(calls) == 2
    finally:
        release.set()
        for _ in range(100):
            with service._SUMMARY_CACHE_LOCK:
                running = service._SUMMARY_CACHE.get('running')
            if not running:
                break
            time.sleep(0.01)
    assert service.reliability_summary(object())['generated_at'] == '2'


@pytest.mark.postgres
def test_production_reliability_snapshot_is_repeatable_and_read_only():
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError
    from sqlalchemy.engine import make_url

    url = os.environ.get('TEST_DATABASE_URL')
    if not url:
        pytest.skip('Isolated PostgreSQL test database is required')
    assert 'test' in (make_url(url).database or '').lower(), 'Never run this check on the application database'
    engine = create_engine(url)
    try:
        with Session(engine) as session:
            service.prepare_reliability_snapshot(session)
            assert session.scalar(text('SHOW transaction_isolation')) == 'repeatable read'
            assert session.scalar(text('SHOW transaction_read_only')) == 'on'
            with pytest.raises(DBAPIError):
                session.execute(text('CREATE TABLE reliability_write_must_be_rejected (id integer)'))
            session.rollback()
    finally:
        engine.dispose()
