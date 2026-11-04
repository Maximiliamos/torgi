# ETP diagnostic contract improvements — 5 November 2026

This focused branch is **read-only diagnostics only**. It does **not** change production import authorization, PostgreSQL data, map publication, the two-pass acceptance gate or schedule.

## Fixed diagnostic defects

- Lot-online does not expose `fetch_lot`; its supported `detail_enrichment` capability is used for a bounded in-memory sample. A listing-only connector receives `N/A` instead of a false failed detail probe.
- Failed detail identity must never count toward `individual_detail: PASS`.
- HTTP error codes are reported as sanitized `provider_http_401/403/429/...` plus `http_status`, with no raw URLs, credentials, HTML or headers.
- CDTRF's generic HTML catalogue adapter was not its validated official API. The diagnostic now uses bounded `/api/Trade/trades` JSON pagination: up to two pages of source-wide IDs and property hints, while details, prices, statuses, terminal page, production import and map remain `NOT_TESTED`.
- CDTRF is **not** considered broken merely because a valid API page contained no property hints. No false 'full catalogue' acceptance is emitted.

## Relevant known source fixes in still-Draft PR #1086

- NISTP: use the actual form method (GET) and preserve selected form fields; POST on the public form was not an appropriate page-two request.
- CenterR: exclude repeated promotional/news sidebar links from true paginated catalogue cards.
- CDTRF: official API is implemented separately in `cdtrf_sync.py`; the 5-page audit's lack of property candidates does **not** prove a broken network or invalidate a valid JSON page.
- Status and price parsing: do not replace missing per-lot prices with trade-level money or deposits.

These changes already exist in **unmerged PR #1086** and should be separately tested and integrated by the desktop agent. Do not duplicate or prematurely merge the broad Draft while Home runs.

## Acceptance limitations

Green probe execution means the diagnostic ran; it does **not** mean all ETPs passed. Check `first_failure`, stage states and `complete_source_inventory` per source. No new direct ETP is automatically authorized for production by this PR.

See [Home 22-ETP probe](https://github.com/Maximiliamos/torgi/actions/runs/41915045518) and [PR #1086](https://github.com/Maximiliamos/torgi/pull/1086).
