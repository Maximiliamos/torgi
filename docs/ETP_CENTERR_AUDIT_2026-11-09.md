# CenterR source audit — 2026-11-09

## Scope

Source: `centerr.ru` / `https://bankrupt.centerr.ru/public/purchases-all/`.

Base SHA: `34baeaf7202b190e83c1aeaf08ec46f2efa2da76`.

**PRODUCTION ENABLED: NO**

This audit is source-specific. It does not change scheduler, allowlists,
reconciliation, archival, `docs/ROADMAP.md`, or the shared stage checklist.

## Previous failure

The 5 November audit found that lot-shaped links from the repeated news/sidebar
area could be collected as if they were catalogue cards. That caused duplicate
or wrong IDs on page 2. PR #1093 added a generic sidebar filter, but the
post-fix live contract was not re-proven end-to-end.

The remaining identity ambiguity was that the catalogue exposes both a visible
trade/procedure number and a lot number, while the actual provider lot identity
is encoded by the canonical detail URL:

`/public/<auctions|contests|public-offers>/lots/view/<provider_lot_id>/`.

## Live evidence before code changes

The public catalogue on 9 November 2026 showed procedure `0101489` with
different individual lots. Lot 1 and lot 3 had different detail URLs, prices
and cadastral numbers. Their individual detail pages repeated trade
`0101489` and their own lot numbers.

This proves that the procedure number alone is not an individual lot identity.

## Implemented contract

- Parse only structurally valid catalogue table rows.
- Ignore news/editorial/sidebar lot-shaped links because they are outside the
  catalogue row schema.
- Use the canonical detail URL provider lot ID as `external_id`.
- Preserve `trade_id` and visible `lot_number` as independent cross-checks.
- Reject one provider lot ID mapped to conflicting trade/lot/title rows.
- Parse detail identity again and reject substitutions.
- Keep price/status/title/cadastre/area/address extraction lot-local.
- Keep production completeness/reconciliation/archival disabled.

## Regression

`tests/fixtures/centerr_duplicate_wrong_id.html` reproduces the old failure
class: a promotional lot-shaped link plus two distinct catalogue cards that
incorrectly point at the same provider lot ID. The strict parser fails closed
on the collision instead of silently deduplicating.

## Live two-pass proof

The source-specific read-only probe records:

- page 1, page 2 and terminal page;
- two complete scans and ID digests;
- unique provider lot IDs and collisions;
- up to 30 current property details;
- catalogue → detail trade/lot/provider-ID correspondence;
- price/status/title success rates;
- cadastral number, area and address evidence.

## Not yet claimed

Until the exact-head live audit is green, this branch does **not** claim
terminal-page proof, two-pass stability, 30/30 detail success, or production
acceptance.
