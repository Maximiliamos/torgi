# P9 GEO Quality

P9 improves useful coordinate coverage without reopening the entire deferred GEO backlog.

## Resolver improvements

- Russian building/address normalization now understands corporation/building/ownership/litera/unit abbreviations.
- Address candidates can remove apartment/room/office tails when Photon needs a building-level query.
- A cadastral provider result that has the correct cadastral number and a useful address but no geometry can feed that address into the local address geocoder.
- A conflicting cadastral number is never allowed to contribute an address hint.
- Existing region/locality/spatial validation remains fail-closed.

## Quality score

New accepted snapshots include a 0–100 audit score in metadata. The score is informational: it never bypasses validation.

The quality audit also computes scores for legacy snapshots and reports the average plus a sample of scores below 60.

## Deferred backlog canary

The P9 canary is intentionally manual and bounded.

It considers only deferred active/scheduled lots with no current coordinate and selects candidates where the new P9 strategy is likely to help:

- structured Russian address tokens such as corporation/building/ownership/unit;
- cadastral failures where NSPD previously returned no coordinates and the cadastral-address bridge may recover the lot.

Defaults:

- Central Federal District first;
- maximum 200 lots;
- hard cap 500;
- preview only unless `apply=true`;
- idempotent marker prevents repeatedly requeueing the same canary lots.

Workflow: `.github/workflows/p9-geo-quality-canary.yml`.

Production sequence after P8 acceptance:

1. Preview 200 CFO candidates.
2. Release the bounded canary.
3. Let the standard GEO worker process them.
4. Compare recovered count, provider mix, quality scores, mismatches and map publication.
5. Expand only if the canary quality is acceptable.

TBankrot is outside P9.
