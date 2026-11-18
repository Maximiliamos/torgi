import asyncio
from copy import deepcopy
from unittest.mock import Mock

import pytest

from bankrotai.connectors.registry import connector_registry
from bankrotai.connectors.etp_sources.sberbank_ast_contract import (
    SberbankAstContractError, detail_url_from_external_id, parse_detail,
)
from bankrotai.connectors.etp_sources.sberbank_ast_detail import build_goods_card_request, parse_goods_card

ID = 'sberbank-ast.ru:12-0-0-62043'


def card():
    # Reduced public ContractVersion=2 response; contacts/documents omitted.
    return {'GoodsCard':{'ContractVersion':'2','Goods':{
        'Id':'62043','Type':{'Id':'12','Name':'Земельные участки'},
        'Name':'Земельный участок, КН 47:07:0479003:523',
        'Description':'Земельный участок 800 кв. м., кадастровый номер 47:07:0479003:523',
        'Price':'99999','Source':{'TradeSection':{'Id':'13'},'Purchase':{'Id':'4295995'}},
        'Region':{'Name':'Ленинградская область','Address':'д. Скотное'},'OperationType':'Продажа'},
        'Bid':{'Id':'3470226','Number':'1','TradeSection':{'Id':'13'},
               'Status':{'Name':'Опубликован(-а)'},'Price':{'Initial':'4770000.00','Current':'4700000.00'},
               'Deposit':'954000.00','Schedule':{'AuctionStart':'2026-12-29T12:00:00'}},
        'Purchase':{'Id':'4295995','Code':'SBR013-2611180007-0','TradeSection':{'Id':'13'},
                    'Organizer':{'Address':'чужой адрес организатора'}},
        'Location':{'Address':'Ленинградская область, д. Скотное','Latitude':'60.17','Longitude':'30.37'},
        'Recommendations':[{'Goods':{'Id':'999','Name':'Квартира 77:01:0000001:999'},
                            'Bid':{'Status':{'Name':'Торги завершены'},'Price':{'Initial':'1'}}}]}}


def test_reads_exact_goods_bid_instead_of_shell_recommendations_or_advertisement_price():
    lot=parse_goods_card(card(),external_id=ID)
    assert lot.title.startswith('Земельный участок') and lot.category!='other'
    assert lot.cadastral_number=='47:07:0479003:523'
    assert lot.start_price==4770000 and lot.current_price==4700000 and lot.deposit_amount==954000
    assert lot.auction_status=='scheduled'
    assert lot.address=='Ленинградская область, д. Скотное'
    assert lot.procedure_number=='SBR013-2611180007-0'
    assert lot.auction_at is None and lot.auction_timezone is None
    assert lot.raw_data['source_schedule']['AuctionStart']=='2026-12-29T12:00:00'
    assert 'Recommendations' not in lot.raw_data and 'Latitude' not in lot.raw_data


@pytest.mark.parametrize('alter', ['id','type','scope','purchase','contract','root'])
def test_rejects_unproven_identity_or_wrong_scope(alter):
    payload=card();c=payload['GoodsCard']
    if alter=='id':c['Goods']['Id']='999'
    if alter=='type':c['Goods']['Type']['Id']='11'
    if alter=='scope':c['Bid']['TradeSection']['Id']='12'
    if alter=='purchase':c['Purchase']['Id']='999'
    if alter=='contract':c['ContractVersion']='3'
    if alter=='root':del c['Goods']
    with pytest.raises(SberbankAstContractError):parse_goods_card(payload,external_id=ID)


def test_recommendation_does_not_fill_missing_own_price_or_status():
    payload=deepcopy(card());payload['GoodsCard']['Bid']['Price']={}
    payload['GoodsCard']['Bid']['Status']={}
    lot=parse_goods_card(payload,external_id=ID)
    assert lot.start_price is None and lot.current_price is None and lot.auction_status=='unknown'


def test_vue_shell_cannot_become_contacts_property():
    shell='<h2>Контакты</h2><p>'+('Недвижимое имущество ' * 30)+'</p><a-form></a-form><script src="GoodsViewCatalog.Apl.js"></script>'
    with pytest.raises(SberbankAstContractError):parse_detail(shell,detail_url=detail_url_from_external_id(ID),external_id=ID)


def test_connector_uses_public_read_only_template_contract():
    c=connector_registry.create('sberbank-ast.ru');response=Mock();response.json.return_value=card()
    c._read_request=Mock(return_value=response)
    lot=asyncio.run(c.fetch_lot(ID))
    url,payload=build_goods_card_request(ID)
    c._read_request.assert_called_once_with('post',url,json=payload,timeout=c.timeout,allow_redirects=False)
    assert payload['actionType']=='TEMPLATE' and payload['windowCode']==payload['actionCode']
    assert lot.external_id==ID


def test_unknown_middle_components_cannot_be_silently_dropped():
    with pytest.raises(SberbankAstContractError):build_goods_card_request('sberbank-ast.ru:12-1-0-62043')


def test_explicit_lease_cannot_pass_sale_filter():
    from bankrotai.services.real_estate_filter import is_sale_real_estate_lot
    payload=card();payload['GoodsCard']['Goods']['OperationType']='Аренда'
    assert not is_sale_real_estate_lot(parse_goods_card(payload,external_id=ID))


def test_optional_redundant_source_purchase_id_can_be_absent_in_v2():
    payload=card();del payload['GoodsCard']['Goods']['Source']['Purchase']['Id']
    lot=parse_goods_card(payload,external_id=ID)
    assert lot.procedure_number=='SBR013-2611180007-0'
    assert lot.raw_data['source_bid_id']=='3470226'
