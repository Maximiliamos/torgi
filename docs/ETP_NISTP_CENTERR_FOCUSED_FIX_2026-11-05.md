# Verified source-contract fixes: NISTP and CenterR (5 November 2026)

These focused changes are also present in the broad unmerged [PR #1086](https://github.com/Maximiliamos/torgi/pull/1086); they are extracted as a small independent change for review and safe CI testing.

## NISTP

The public `nodesearch` form declares `method=get` for its pagination. A previous POST submitted the page number but returned a different document with no valid property lot links. Honour the HTML form's actual method, preserve checked repeated filters, and reject unsupported methods. All provider URLs still pass the trusted-host HTTPS check.

The production catalogue scope is now explicitly limited to the three browser-confirmed current states: `Торги объявлены`, `Прием заявок`, and `Идут торги`. The initial URL carries three repeated `trade_state[]` parameters, every paginated response must visibly retain all three checked inputs, and any out-of-scope row status fails the proof. The user-observed filtered catalogue showed 143 pages on 5 November 2026; this is evidence for the expected boundary, not yet a successful two-pass result. Earlier read-only runs over 370 unfiltered pages are diagnostic only and cannot be used for active-source acceptance.

## CenterR

The public CenterR catalogue includes repeated editorial/news sidebar links sharing the same URL shape as actual auctions; they are not rows in the paginated lot results. Skip only `news-short`/`news-body` ancestors, retain actual catalogue links, and test that duplicate promotional IDs do not contaminate the observed candidate set. The next/live page and per-lot detail still require independent validation.

## Production boundary

No DB import, GEO, map writes, missing-lot reconciliation or automatic source acceptance. The existing fail-closed safeguards remain unchanged. Accept the filtered active source only after two independent complete scans with identical ID/price/status evidence, detail and price evidence, then staged DB/GEO/map verification. The obsolete unfiltered tail-preflight launcher was removed.

The full Home source diagnostic is in [run 41915045518](https://github.com/Maximiliamos/torgi/actions/runs/41915045518), and per-source snapshot in `docs/ETP_STAGE_DIAGNOSTIC_SNAPSHOT_2026-11-05.md`.
