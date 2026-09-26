# Production audit: all real-estate sources → geocoding → map

Started: 2026-09-27

## Goal

Prove with production evidence that the complete real-estate pipeline is stable:

`source parsers → persistent ingestion → canonical lots → geocoding → map dataset → REG.RU S3/public map`.

The audit must also explain why the UI has shown geocoding around 66.8% with an ETA near two hours for multiple days.

## Safety rules

- Preserve fail-closed source reconciliation and GEO validation.
- Never bypass coverage guards.
- Never mark partial source responses as complete.
- Never mass-change coordinates merely to improve a percentage.
- Collect a read-only baseline before starting any modifying run.
- Run full source tests one source at a time before the combined reconciliation.
- Use bounded GEO batches before allowing a full drain.
- Stop on DB/internal errors, stale leases, persistent GEO stalls, or abnormal spatial-quality regressions.

## Execution stages

| Stage | Action | Acceptance evidence |
| --- | --- | --- |
| 0 | Read-only production baseline | exact counters, active leases/tasks, latest batches, source health, current map |
| 1 | Explain the 66.8% UI state | mapped %, resolved %, actionable backlog, liveness, ETA inputs |
| 2 | Full parser run for each configured source | pages/items/errors/duration/complete-source-run for all 5 sources |
| 3 | One production full reconciliation | all configured source rows terminal; coverage/freshness updated |
| 4 | GEO baseline after fresh ingestion | exact mapped/terminal/retry/actionable populations |
| 5 | GEO benchmark | bounded 50/100/250/500 batches with throughput/provider/error metrics |
| 6 | Controlled GEO drain | actionable backlog reaches zero or every blocker is classified |
| 7 | Spatial quality audit | no gross region/CFO/hotspot regressions |
| 8 | DB → current map → public transport reconciliation | expected map membership matches current dataset and public bundle |
| 9 | Hidden-error review | no unexplained running jobs/leases, silent failures, stale datasets |
| 10 | Fixes + repeat affected checks | regression tests plus green production evidence |

## Configured source set

The current production `default_source_specs()` contains:

1. `torgi.gov.ru`
2. `tbankrot.ru`
3. `lot-online.ru`
4. `torgi-russia.ru`
5. `bidexpert.ru`

## Geocoding progress semantics to verify

Current code exposes at least four distinct facts:

- `total`: active non-duplicate lots with address or cadastral input;
- `geocoded`: lots with a GEO snapshot;
- `terminal_failures`: lots no longer actionable under the normal retry policy;
- `actionable_remaining = total - geocoded - terminal_failures`.

The displayed mapped percentage is currently `geocoded / total`, while ETA is based on
`actionable_remaining / recent_rate`. Therefore a stable percentage does not by itself prove a
running backlog. Stage 1 must determine whether the observed 66.8% represents a stalled queue,
a large terminal population, or both.

## Completion definition

The audit is complete only when:

- every configured source has a measured full-run result;
- full reconciliation has terminal evidence for every configured source;
- GEO throughput and failure composition are measured from production;
- any stalled/progress-display defect is fixed and regression-tested;
- valid GEO lots are represented in the current map dataset/public map as expected;
- production health is green and there are no unexplained active audit blockers.
