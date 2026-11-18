"""Public GoodsCard v2 data used by the first-party GoodsView Vue form."""
from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urlsplit

from bankrotai.domain import NormalizedLot
from bankrotai.services.asset_category import classify_category
from bankrotai.connectors.etp_sources.sberbank_ast_contract import (
    BANKRUPTCY_SECTION_ID, PLATFORM_CODE, PLATFORM_NAME, SOURCE, SOURCE_ID,
    SberbankAstContractError, detail_url_from_external_id, normalize_status,
    normalize_text, parse_decimal,
)

GOODS_CARD_API = 'https://utp.sberbank-ast.ru/api/Processing/main'
_CAD = re.compile(r'(?<!\d)(\d{2}:\d{1,2}:\d{5,9}:\d{1,12})(?!\d)')


def build_goods_card_request(external_id: str) -> tuple[str, dict[str, str]]:
    path = urlsplit(detail_url_from_external_id(external_id)).path
    parts = path.rstrip('/').split('/')[-4:]
    if parts[1:3] != ['0', '0']:
        raise SberbankAstContractError('GoodsCard middle identity components are not proven')
    return GOODS_CARD_API, {'windowCode':path, 'actionType':'TEMPLATE', 'actionCode':path}


def _record(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def parse_goods_card(payload: Any, *, external_id: str) -> NormalizedLot:
    build_goods_card_request(external_id)
    for _ in range(2):
        if not isinstance(payload, str):
            break
        try:
            payload = json.loads(payload)
        except (ValueError, TypeError) as exc:
            raise SberbankAstContractError('Invalid GoodsCard JSON') from exc
    card = _record(_record(payload).get('GoodsCard'))
    if str(card.get('ContractVersion')) != '2':
        raise SberbankAstContractError('Unsupported or absent GoodsCard contract')
    goods, bid, purchase = (_record(card.get(k)) for k in ('Goods','Bid','Purchase'))
    parts = external_id.removeprefix(SOURCE_ID+':').split('-')
    if str(goods.get('Id')) != parts[3] or str(_record(goods.get('Type')).get('Id')) != parts[0]:
        raise SberbankAstContractError('GoodsCard identity differs from requested lot')
    origin = _record(goods.get('Source'))
    sections = [_record(origin.get('TradeSection')), _record(bid.get('TradeSection')),
                _record(purchase.get('TradeSection'))]
    if any(str(s.get('Id')) != BANKRUPTCY_SECTION_ID for s in sections):
        raise SberbankAstContractError('GoodsCard does not prove bankruptcy scope')
    source_purchase_id = _record(origin.get('Purchase')).get('Id')
    if (not purchase.get('Id') or not bid.get('Id')
            or (source_purchase_id not in (None, '') and str(source_purchase_id) != str(purchase.get('Id')))):
        raise SberbankAstContractError('GoodsCard purchase binding differs')
    title = normalize_text(goods.get('Name'))
    description = normalize_text(goods.get('Description'))
    if not title or not description:
        raise SberbankAstContractError('GoodsCard has no individual title/description')
    category = classify_category(title, description)
    price = _record(bid.get('Price'))
    status_label = normalize_text(_record(bid.get('Status')).get('Name'))
    # These are exact v2 enum labels, never surrounding recommendation text.
    labels = {'опубликован(-а)':'scheduled', 'завершен(-а)':'closed',
              'завершён(-а)':'closed', 'отменен(-а)':'closed', 'отменён(-а)':'closed'}
    status = labels.get(status_label.casefold(), normalize_status(status_label))
    location = _record(card.get('Location'))
    region = _record(goods.get('Region'))
    address = normalize_text(location.get('Address') or region.get('Address')) or None
    cadastres = list(dict.fromkeys(_CAD.findall(title+' '+description)))
    url = detail_url_from_external_id(external_id)
    return NormalizedLot(
        external_id=external_id, source=SOURCE, source_system=SOURCE_ID,
        title=title[:500], description=description, category=category,
        region_name=normalize_text(region.get('Name')) or None,
        region_slug=None, address=address,
        cadastral_number=cadastres[0] if cadastres else None,
        vin=None, area=None,
        start_price=parse_decimal(price.get('Initial')),
        current_price=parse_decimal(price.get('Current')),
        auction_status=status, source_url=url, lot_url=url, detail_level='detail',
        platform_name=PLATFORM_NAME, platform_code=PLATFORM_CODE,
        procedure_number=normalize_text(purchase.get('Code')) or None,
        deposit_amount=parse_decimal(bid.get('Deposit')),
        raw_data={'detail_contract':'GoodsCard-v2','detail_enrichment_version':2,
            'individual_lot_identity_verified':True,'bankruptcy_filter_verified':True,
            'source_goods_id':str(goods['Id']),'source_goods_type_id':str(parts[0]),
            'source_bid_id':str(bid['Id']),'source_purchase_id':str(purchase['Id']),
            'source_lot_number':normalize_text(bid.get('Number')) or None,
            'source_status_label':status_label,'cadastral_numbers':cadastres,
            # No recommendation, seller/contact address, or location centroid is used.
            # Unzoned ISO business dates remain raw until their timezone is proven.
            'source_schedule':bid.get('Schedule'),
            'source_operation_type':goods.get('OperationType'),
            'typeTransaction':goods.get('OperationType'),
            'complete_source_inventory':False,'safe_for_archival':False,'reconcile_missing':False},
    )
