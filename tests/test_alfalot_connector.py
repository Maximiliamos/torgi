import asyncio
import json
from datetime import datetime, timezone

import pytest
import requests

from bankrotai.connectors.etp_sources.alfalot import (
    API_URL, AlfalotConnector, catalogue_params, normalize_detail, parse_catalogue,
)
from bankrotai.connectors.etp_sources.common import EtpSearchFilters, EtpSourceProfile, SourceSchemaError


def row(n=1, title='Квартира 50 кв.м., 76:23:010101:1, г. Ярославль, ул. Победы, д. 12'):
    return dict(identifier='0012345678DS',lotNumber=n,type='SALE',uuid=f'coretest{n:05}',title=title,
        lotLink=f'/app/LotCard/page?LotCard.lotEntity=LT%3Acoretest{n:05}',
        gdStartDate='20.11.2026 10:00',gdEndDate='20.12.2026 18:00&nbsp;МСК')


def html(n=1, *, initial='10 000,00 руб.', status='Идет прием заявок'):
    return f'''<h1>Лот 0012345678DS.lot{n}</h1><table>
    <tr><td>Номер процедуры</td><td>0012345678DS</td></tr>
    <tr><td>Номер лота</td><td>{n}</td></tr>
    <tr><td>Наименование имущества</td><td>Квартира 50 кв.м., 76:23:010101:1, г. Ярославль, ул. Победы, д. 12</td></tr>
    <tr><td>Начальная цена лота</td><td>{initial}</td></tr>
    <tr><td>Размер задатка</td><td>999 999 руб.</td></tr>
    <tr><td>Статус лота</td><td>{status}</td></tr></table>'''.encode()


class Connector(AlfalotConnector):
    profile=EtpSourceProfile(source_id='alfalot.ru',platform_name='Alfalot',platform_code='alfalot',
        listing_url='https://etp.alfalot.ru/app/SearchLots/page',lot_url_pattern='LotCard',allowed_hosts=('etp.alfalot.ru',))


class FakeSession:
    def __init__(self):
        self.headers={};self.calls=[]

    def get(self,url,**kwargs):
        self.calls.append((url,kwargs))
        response=requests.Response();response.status_code=200;response.url=url
        if url==API_URL:
            params=kwargs['params'];window=json.loads(params['limit'])
            assert json.loads(params['filter'])=={'state':['GD']}
            assert json.loads(params['query'])=={'types':['SALE','DEBTOR_SALE']}
            assert window['max']==20
            rows=([row(n,'Автомобиль VIN XTA00000000000001') for n in range(1,21)]
                  if window['min']==0 else [row(21)])
            response._content=json.dumps({'count':len(rows),'totalCount':21,'list':rows}).encode()
        else:
            response._content=html(21)
        return response


def test_selected_sale_walk_continues_after_an_entire_nonproperty_page():
    session=FakeSession();connector=Connector(session=session)
    first=asyncio.run(connector.search(EtpSearchFilters()))
    assert first.items==[] and first.next_cursor=='2'
    second=asyncio.run(connector.search(EtpSearchFilters(),first.next_cursor))
    assert second.next_cursor is None and len(second.items)==1
    lot=second.items[0]
    assert lot.external_id=='alfalot.ru:LT:coretest00021' and lot.start_price==10000
    assert lot.auction_status=='active' and lot.category=='apartment'
    assert lot.application_deadline==datetime(2026,12,20,18) and lot.auction_timezone=='Europe/Moscow'
    assert lot.raw_data['detail_identity_verified'] is True
    assert lot.raw_data['verified_source_fields']['cadastral_number']['value']=='76:23:010101:1'
    assert second.metadata['reconcile_missing'] is False and second.metadata['complete_source_inventory'] is False
    assert len(connector._seen_entities)==21 and len(session.calls)==3


@pytest.mark.parametrize('change',['duplicate','foreign','uuid','purchase','short_window'])
def test_catalogue_identity_and_complete_window_are_checked_before_any_detail(change):
    rows=[row(1),row(2)];payload={'count':2,'totalCount':2,'list':rows}
    if change=='duplicate':rows[1]=rows[0]
    elif change=='foreign':rows[0]['lotLink']='https://evil.test/app/LotCard/page?LotCard.lotEntity=LT%3Acoretest00001'
    elif change=='uuid':rows[0]['uuid']='another'
    elif change=='purchase':rows[0]['type']='BUYING'
    else:payload['totalCount']=42
    with pytest.raises(SourceSchemaError):parse_catalogue(payload,page=1)


def test_detail_cannot_borrow_another_numbered_lots_price():
    with pytest.raises(SourceSchemaError,match='lot-number'):
        normalize_detail(row(1),html(2),checked_at=datetime.now(timezone.utc))


def test_missing_initial_price_never_uses_the_deposit_or_announces_a_terminal_sale():
    lot=normalize_detail(row(),html(initial='не указана',status='Прием заявок окончен'),checked_at=datetime.now(timezone.utc))
    assert lot.start_price is None and not lot.raw_data['detail_start_price_verified']
    assert lot.auction_status=='unknown'


def test_servlet_max_is_a_fixed_count_so_last_page_is_not_repeated():
    assert json.loads(catalogue_params(3)['limit'])=={'min':40,'max':20,'updateTotalCount':True}
    assert json.loads(catalogue_params(1)['filter'])=={'state':['GD']}


def test_numbered_http_window_can_be_opened_directly_without_old_session_state():
    connector=Connector(session=FakeSession())
    result=asyncio.run(connector.search(EtpSearchFilters(page=2)))
    assert len(result.items)==1 and result.items[0].external_id=='alfalot.ru:LT:coretest00021'
    assert result.next_cursor is None
