# UTPL / Объединённая торговая площадка — audit, 2026-11-09

**Scope:** `utpl.ru` only. **PRODUCTION ENABLED: NO.** No source scheduling, archive, missing-lot reconciliation, allowlist, or GEO changes.

## Baseline and existing state

Base commit: `5997d239a71e253b7dd3386820ca08002f63f071` (GitHub latest commit observed when branch was created). Previous operator status supplied for this assignment: catalogue PASS (40 candidates), page 2 PASS, detail PASS, status PASS, price FAIL, classification FAIL. These historical figures **are not independently reproduced** here.

Existing read-only pilot: `src/bankrotai/connectors/registry/direct_etp_catalogue.py` table_lots; operational parser: `src/bankrotai/connectors/etp_sources/common.py`; profile in `profiles.py`. The latter extracted prices, status, category from the entire detail page instead of isolating the individual-lot region. Another risk: generic text-category classification treated a claim involving land as land ownership.

## Public provider contract evidence

- Official catalogue: `https://bankrupt.utpl.ru/public/purchases-all/` — the entry point currently configured; the exact filter/form request and terminal paginator are **NOT PROVEN** by this audit.
- Separate public-offer catalogue: `https://bankrupt.utpl.ru/public/public-offers-all/`. Provider displays procedure number, **№ лота**, initial and current price in separate columns. The public snapshot includes procedure `0009325`, lot `1`, initial RUB 916,412.00 and current RUB 778,950.20. **These are catalogue observations only.**
- Official lot detail `https://bankrupt.utpl.ru/public/public-offers/lots/view/127514/`: explicit `Информация о лоте №3`, `Наименование: Право требования`, status `Прием заявок`, initial RUB 242,424.25, current RUB 10,181.87 (provider snapshot).
- Official lot detail `https://bankrupt.utpl.ru/public/public-offers/lots/view/127814/`: status `Прием заявок`, initial RUB 625,560.07, current RUB 500,448.07.
- Official auction detail `https://bankrupt.utpl.ru/public/auctions/lots/view/128964/`: lot #1, `Извещение опубликовано`, initial RUB 9,904,782.72.
- Official auction detail `https://bankrupt.utpl.ru/public/auctions/lots/view/127518/`: status `Не состоялся`, initial RUB 695,066.75.
- Official public-offer detail `https://bankrupt.utpl.ru/public/public-offers/lots/view/127755/`: status `Окончен`, initial RUB 22,194,000.00; current RUB 13,316,400.00.

Sources are official publicly indexed pages observed via web retrieval, **not a fresh end-to-end authenticated/browser network trace**. No provider modification was performed. No bulk crawler was executed.

## Changed contract

New `etp_sources/utpl_contract.py`: a single numbered individual-lot section must exist; status, labelled initial and labelled current price, and title are extracted from that section only. No fallback to procedure-level values, deposits or price interval tables. Rights/lease do not become ordinary land or premises. A detail URL/external ID mismatch fails closed. Generic parsers for other providers are untouched.

## Evidence / quantitative gates

| Gate | Result |
| --- | --- |
| Fresh read-only provider snapshots examined | 6 detail URLs and one public-offer listing URL |
| Full HTTP live catalogue walk | **NOT TESTED** |
| Confirmed page 1/page 2/terminal request | **NOT TESTED** |
| Two passes and matching ID digest | **NOT TESTED** |
| ≥30 distinct individual detail cards | **NOT TESTED** |
| Multi-lot per-lot identity comparison | **NOT TESTED** |
| 30-lot price/status success rate | **NOT MEASURED** |
| Full/structured property classification | **NOT PROVEN** |
| Safe for archive/reconciliation | **NO** |
| Focused and full CI on PR head | **NOT RUN** |

No 100% price or status claim should be made. The source is **not accepted**. This PR is a protective scoped parser change and should remain draft pending a Home no-VPN read-only 30+ details audit, the official request/filter/terminal-page proof, two-pass digest, test execution and CI review.

## Files

- `src/bankrotai/connectors/etp_sources/utpl_contract.py`
- `src/bankrotai/connectors/etp_sources/common.py`
- `tests/test_utpl_lot_contract.py`
- `docs/ETP_UTPL_AUDIT_2026-11-09.md`

**PRODUCTION ENABLED: NO**
