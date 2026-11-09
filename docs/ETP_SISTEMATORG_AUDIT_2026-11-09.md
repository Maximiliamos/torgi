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

## Preserved live evidence

GitHub Actions run `42549043000` on pre-isolation head
`c2835fabc9617fbf0474f00a844ef20456053c41` completed the public two-pass
audit successfully. The source contract/evidence collected there is preserved:

- catalogue pages: **847 + 847**;
- terminal page: **847** in both passes;
- procedures: **16,506** per pass;
- individual lots: **20,190** per pass;
- unique individual IDs: **20,190** per pass;
- property lots: **10,648** per pass;
- non-property lots: **9,542** per pass;
- single-lot procedures: **14,423**;
- multi-lot procedures: **2,083**;
- two-pass identity digest: identical
  (`eb3776731ec2af2235ffbd8920986725e0c411f46bc7b3e262a95e1be11aa73a`);
- individual details verified: **30/30**;
- initial prices verified: **30/30**;
- statuses verified: **30/30**;
- public-offer schedules verified in the sample: **6**.

Fail-closed catalogue rejects were counted rather than guessed through:
**419** declared-lot-count mismatches, **10** empty lot titles and **1**
duplicate lot ID per pass. They are not included in the 20,190 accepted
individual identities.

The isolation refactor does not replace this evidence with fixtures. Exact-head
CI/live audit must be repeated after the refactor before the PR is called ready.

## Isolation

Source-specific module:
`src/bankrotai/connectors/etp_sources/sistematorg_contract.py`.

The module now owns the SISTEMATORG catalogue parser, procedure/lot identity,
property classification, detail isolation, prices, deposit, auction step,
status/dates, public-offer periods, pagination detection, terminal-page
navigation and the provider-owned read-only HTTP client.

The full auditor calls this module directly. It no longer needs the generic
`common.py` dispatcher or shared `pagination.py`.

Integration-owned files modified by this PR after isolation: **NONE**.

Pre-existing SISTEMATORG compatibility guards still exist in current `main`
(`common.py`, `source_specific.py`, and the source profile). They are not
changed or removed here because doing so would be a shared integration change
and could regress #1166 before the central integration PR.

## Integration hook required

A later integration PR should make the existing SISTEMATORG branch in
`common.py` delegate to:

- `parse_sistematorg_catalogue_page(...)` for catalogue procedure/lot parsing;
- `property_catalogue_lots(...)` for property candidates;
- `parse_sistematorg_detail_lots(...)` plus
  `parse_sistematorg_external_id(...)` for exact requested-lot isolation.

The existing fetch URL should continue to use the procedure `trade_nid`.
No other source behaviour is required and no production activation belongs in
this PR.

PRODUCTION ENABLED: NO
