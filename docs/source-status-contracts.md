# Auction source status contracts

This document records the evidence required before expanding the persisted
auction lifecycle beyond the backward-compatible `active`, `scheduled`,
`closed`, and `unknown` values. Unknown or incomplete source responses must
never be interpreted as terminal.

| Source | Current evidence | Safe non-terminal evidence | Safe terminal evidence | Remaining evidence |
| --- | --- | --- | --- | --- |
| `torgi.gov.ru` | Public JSON lot-card API; explicit lot status and application dates | `PUBLISHED`, `APPLICATIONS_SUBMISSION` | An explicit terminal lot-card status from a complete API response | Captured fixtures for cancelled, failed/not-held and completed lots |
| `tbankrot.ru` | Search card and exact detail page; search can be access-limited | visible active/scheduled card, public-offer interval with a future end | exact detail text such as `Торги завершены`; elapsed final public-offer interval | Authorized bulk/export access and fixtures for cancelled/not-held lots |
| `lot-online.ru` | Search and detail HTML; archive mode is explicit | active listing/detail with future application or auction dates | explicit archive/expired status from detail or archive response | Fixtures covering every archive reason |
| `torgi-russia.ru` | JSON search API behind the current site design | listing returned by the active endpoint | explicit terminal detail status only | Completed/cancelled/not-held detail fixtures |
| `bidexpert.ru` | Public category/search pages | visible current listing | explicit terminal detail status only | Detail fixtures and reliable end-date semantics |

## Canonical target vocabulary

The intended vocabulary is:

`scheduled`, `application_open`, `auction_pending`,
`public_offer_active`, `completed`, `cancelled`, `failed`, `expired`,
`archived`, and `unknown`.

It is not yet safe to migrate existing production rows to this vocabulary.
Most stored `source_status` values have already been collapsed to the legacy
four-state model, so the original distinction cannot be reconstructed without
fresh source observations. The current safe rules are:

1. `auction_at` is a start timestamp and never proves completion.
2. Only explicit terminal source evidence or the end of the final public-offer
   interval may archive a live source card.
3. Missing listing rows require two complete, coverage-validated syncs.
4. Partial/access-limited syncs never reconcile missing rows.
5. An active canonical sibling keeps the asset visible.

Every new source-specific terminal mapping must arrive with a captured,
minimal fixture and a regression test before it can affect production data.
