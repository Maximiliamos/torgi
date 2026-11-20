import asyncio
from types import SimpleNamespace
import pytest
from bankrotai.services import sberbank_ast_health, source_resilience


@pytest.mark.parametrize('fault', [None, 'empty', 'identity', 'price', 'status', 'foreign'])
def test_sber_health_requires_actual_own_detail(fault):
    lot = SimpleNamespace(external_id='sberbank-ast.ru:12-0-0-62427')
    raw = {key: True for key in ('detail_identity_verified', 'detail_start_price_verified', 'detail_status_verified')}
    if fault in {'identity', 'price', 'status'}:
        raw[{'identity':'detail_identity_verified', 'price':'detail_start_price_verified', 'status':'detail_status_verified'}[fault]] = False
    detail = SimpleNamespace(external_id=lot.external_id, source_system='foreign' if fault=='foreign' else 'sberbank-ast.ru', start_price=100.0, raw_data=raw)
    class Connector:
        async def search(self, filters, cursor):
            return SimpleNamespace(items=[] if fault=='empty' else [lot])
        async def enrich_lot(self, item):
            return detail
    if fault:
        with pytest.raises(ValueError):
            asyncio.run(sberbank_ast_health._probe(Connector()))
    else:
        result = asyncio.run(sberbank_ast_health._probe(Connector()))
        assert result['success'] and result['verified_details']==1


def test_sber_probe_uses_catalogue_not_legacy_head(monkeypatch):
    calls=[]
    def probe(*,timeout_seconds):
        calls.append(timeout_seconds)
        return {'success':True}
    monkeypatch.setattr(sberbank_ast_health,'probe_sberbank_ast_catalogue',probe)
    assert source_resilience.probe_source_endpoint('sberbank-ast.ru',timeout_seconds=7.0)['success']
    assert calls==[7.0]
