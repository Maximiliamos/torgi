# SISTEMATORG source audit — 2026-11-09

Base SHA: `5997d239a71e253b7dd3386820ca08002f63f071`

## Previous defect

Production/history treated `tradelist.php` rows as assets although they are procedures. Historical evidence in issue #1065 recorded 3,361 procedure rows and only 18 rows with any price. Multi-lot procedures can mix real estate, vehicles and receivables. A procedure-level amount or the first detail amount therefore cannot be assigned to every asset.

PR #1166 added a deliberately narrow safety exception: detail enrichment only when the source proves exactly one lot. That behaviour is retained as a compact single-lot fallback; it is not generalized to ambiguous multi-lot pages.

## Live first-party contract confirmed on 2026-11-09

Official catalogue: `https://sistematorg.com/tradelist.php`.

Pagination is the public `tradesearch` GET form with `pagenum`. Terminal status must be established from the actual pagination controls; a numeric batch cap is never accepted as source end.

Official procedure detail: `https://sistematorg.com/trade_view.php?trade_nid=<trade_id>`.

The catalogue exposes one procedure row with `Всего лотов: N` and embedded `Лот № N` descriptions. Current live catalogue examples include mixed procedures (vehicle + receivables) and multi-lot land procedures. Classification must therefore be per numbered lot.

First-party detail pages expose repeated individual lot sections with:
- `Лот № N`;
- `Номер лота`;
- `Наименование имущества`;
- `Cведения об имуществе`;
- `Начальная цена`;
- `Размер задатка`;
- `Статус торгов`;
- for public offers, per-lot period rows with start/end, price and deposit.

Verified examples:
- `trade_nid=36750`: the previously reported single-lot case remains compatible with the strict single-lot fallback and has an explicitly labelled initial price.
- `trade_nid=63098`: multi-lot public offer; issue #1065 and the live page show separate numbered lot sections, separate prices/statuses and separate public-offer schedules. The six lots are receivables, which is a required negative property-classification fixture.
- additional live first-party pages observed during this audit included `60471`, `62957`, `63699`, `63438`, `63285`, `61233`, `62813`, `61715`, and `61775`, demonstrating both auctions and public-offer schedule layouts.

## Identity and price rule

Canonical asset identity is:

`sistematorg.com:<trade_nid>:<displayed_lot_number>`

A price is accepted only from the matching numbered lot section. The parsed record carries `price_trade_id` and `price_lot_id`. Deposit, auction step, procedure totals and sibling-lot values are never used as an asset price.

Catalogue candidates intentionally have no price and unknown status until detail isolation succeeds.

## Property classification

Classification is performed on one numbered lot only. Explicit receivables, lease rights, vehicles and equipment are excluded from property lots. Land and real-estate classes are retained. Mixed procedure context is not inherited by sibling lots.

## Public offers

The parser stores the complete verified per-lot period schedule. It does not invent `current_price` by choosing a period based on wall-clock time. `current_price` is populated only when the source explicitly labels a current price.

## Full read-only auditor

`python -m bankrotai.services.etp_sistematorg_price_audit --passes 2 --max-details 30 --max-pages 1000`

The auditor counts separately:
- catalogue pages;
- procedures;
- individual lots;
- property/non-property lots;
- single-lot/multi-lot procedures;
- unique IDs and identity digest;
- terminal page;
- verified detail IDs;
- verified initial prices;
- verified statuses;
- verified public-offer schedules;
- rejects grouped by reason.

It performs no database writes, reconciliation, archival or map publication.

## Code changes

- new strict `sistematorg_contract.py`;
- catalogue parser now emits stable per-lot candidates from procedure rows;
- detail parser isolates the requested lot from the procedure detail;
- #1166 single-lot compact fallback retained;
- full read-only source auditor replaces the old first-page/single-lot-only probe;
- regression tests cover mixed procedures, independent prices, public-offer schedules, negative receivables classification and the #1166 fallback.

## Still not proven in this PR

The connected execution environment used for this change cannot run outbound repository code against Sistematorg, so the newly added two-pass full-catalogue auditor was not executed here. Accordingly this PR does **not** claim:
- a fresh exact procedure/individual-lot total;
- a fresh terminal-page number;
- 30/30 live detail success;
- production acceptance;
- reconciliation/archive safety.

Those values must come from the included read-only auditor on an environment with direct provider network access. Parser guards remain fail-closed until then.

PRODUCTION ENABLED: NO
