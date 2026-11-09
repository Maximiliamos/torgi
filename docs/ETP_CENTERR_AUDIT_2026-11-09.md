# CenterR source audit — 2026-11-09

## Scope

Source: `centerr.ru` / `https://bankrupt.centerr.ru/public/purchases-all/`.

Exact work base SHA: `34baeaf7202b190e83c1aeaf08ec46f2efa2da76`.

**PRODUCTION ENABLED: NO**

This audit is source-specific. It does not change scheduler, allowlists,
reconciliation, archival, `docs/ROADMAP.md`, or the shared stage checklist.

## What was broken

The earlier live audit had page-2 duplicate/wrong IDs. PR #1093 filtered
lot-shaped links from repeated CenterR news/sidebar blocks, but it did not
complete a post-fix live proof of individual lot identity or terminal
pagination.

CenterR exposes three different identity values that must not be conflated:

- visible trade/procedure number;
- visible lot number inside that procedure;
- provider lot ID in the canonical detail URL
  `/public/<auctions|contests|public-offers>/lots/view/<provider_lot_id>/`.

Using a procedure number as the individual lot ID is incorrect.

## Live contract evidence

A read-only run on 9 November 2026 proved the public catalogue is an ASP.NET
postback catalogue. Page transitions preserve hidden form state and use
`__EVENTTARGET` / `__EVENTARGUMENT`.

The catalogue is mixed: auctions, contests and public offers coexist and the
rows include real estate/land as well as vehicles, receivables and other
property. Detail pages expose a structured `Классификатор ЕФРСБ`; the parser
retains that value as the authoritative property-type evidence when present.

A concrete multi-lot example was trade `0101489`: different visible lot
numbers had different canonical provider lot URLs, prices and cadastral
numbers. The individual details independently repeated the trade number and
their own lot number. Therefore the trade/procedure ID alone is not a lot ID.

### Full pagination proof

Successful read-only audit head: `35cb5a41bf18a992c5e41011aee92a71d020f04a`.

Both fresh-session scans produced:

- terminal page: **29**;
- catalogue rows: **580**;
- unique provider lot IDs: **580**;
- duplicate same-identity rows: **0**;
- ID collisions: **0**;
- identical sorted-ID digest:
  `ca177ea5be77273d0932cacd08c868cb67caae5ff6e489f1beff07ed346252e5`;
- two-pass ID stability: **true**.

Using separate HTTP sessions is required for the proof because CenterR keeps
ASP.NET paging state server-side. Reusing the first session can reopen the
last visited page and produces a false second-pass result.

### Individual detail proof

The same live run found **284** catalogue rows classified as property
candidates. It attempted 31 current property details to obtain 30 independently
verified cards:

- verified detail identity: **30/30**;
- catalogue/detail price correspondence: **30/30**;
- catalogue/detail status correspondence: **30/30**;
- catalogue/detail title correspondence: **30/30**;
- wrong-detail substitutions: **0**;
- area consistency: **30/30**;
- address extracted where the lot description supplied a recognizable address:
  **16/30**.

One additional card, `centerr.ru:auctions-1171674`, returned HTTP 200 but did
not expose both independently recognizable trade/lot markers to the strict
parser during that run. It was **not** substituted with another lot and was
not counted as verified; parsing failed closed. The audit then continued to
the next property card until 30 verified details were obtained.

## Implemented contract

- Catalogue parsing is limited to structurally valid catalogue table rows.
- News/editorial/sidebar links cannot become catalogue identities.
- `external_id` is based on the canonical provider lot URL ID and sale type.
- `trade_id` and visible `lot_number` are retained separately and checked
  again on detail.
- One provider lot ID mapped to conflicting trade/lot/title data raises a
  fail-closed contract error.
- A requested external ID that differs from the detail URL identity is rejected
  as a wrong-detail substitution.
- Price is taken only from the lot row/detail's explicit price labels; deposit,
  auction step and neighbouring blocks are never substituted for price.
- Status and title are lot-local.
- Detail keeps structured `Классификатор ЕФРСБ`, application start/end and
  auction date when present.
- Address, cadastral number and area are extracted only from the individual
  lot description.
- Production completeness, reconciliation and archival remain disabled.

## Regression fixture

`tests/fixtures/centerr_duplicate_wrong_id.html` reproduces the previous
failure class: a promotional lot-shaped link plus two different catalogue cards
that incorrectly point at the same provider lot ID. The strict parser must
raise on the collision instead of silently deduplicating or substituting one
detail for another.

## Changed files

- `src/bankrotai/connectors/etp_sources/centerr_contract.py`
- `src/bankrotai/connectors/etp_sources/common.py`
- `scripts/audit_centerr_contract.py`
- `tests/test_centerr_contract.py`
- `tests/test_etp_sources.py` (CenterR-only legacy fixtures aligned to the proven row schema)
- `tests/fixtures/centerr_duplicate_wrong_id.html`
- `.github/workflows/centerr-contract-audit.yml`
- this document

## Test compatibility

The first full SQLite run after introducing the strict catalogue parser exposed
three old CenterR tests that used synthetic one-cell tables. Those fixtures no
longer represented the live provider contract. The parser was not relaxed;
only the CenterR test rows were upgraded to the proved nine-column schema.

## Known limitations

- The public catalogue is mixed; catalogue text classification is a discovery
  aid, while the structured EFRSB classifier on detail is preferred evidence.
- Not every lot description contains a parseable address/cadastral/area value.
  Missing values remain missing; they are not inferred from unrelated blocks.
- A public HTTP 200 page can still lack enough identity evidence, as observed
  for provider lot 1171674. Such a card remains fail-closed.
- This audit does not claim source-wide production acceptance.

The final PR head must rerun the focused regression tests and the same read-only
two-pass audit after all source-specific code changes.
