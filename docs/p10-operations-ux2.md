# P10 Operations UX 2.0

P10 turns the existing reliability page into an operator dashboard backed by persisted host/runtime snapshots.

## Dashboard

The Reliability view now combines:

- source freshness and complete-snapshot state;
- per-source circuit state, next retry and last error;
- global source-network circuit;
- GEO coverage, actionable/deferred/network-wait queues;
- current map dataset/version/point count;
- Windows host disk headroom from maintenance;
- latest backup + isolated restore verification;
- latest Home runner network diagnostics;
- recent sync/GEO journal entries.

The page refreshes every 30 seconds while open.

## Safe actions

Admin-only controls:

- pause/resume GEO;
- probe one configured public source;
- a successful manual probe queues only that source in safe fast mode.

Unconfigured sources are rejected. TBankrot is not part of this probe path.

## Host status persistence

No new database table is required. The latest snapshots are stored in AppSetting JSON records:

- `operations_status:maintenance`
- `operations_status:backup`
- `operations_status:runner`

Maintenance, backup and runner diagnostics attempt to persist their snapshots, but telemetry persistence is non-fatal: it can never invalidate an otherwise valid backup or maintenance result.

Disk thresholds shown by the dashboard:

- critical below 15 GB;
- recommended 25 GB.

Backup is healthy only when restore verification passed and the snapshot is at most 60 hours old, matching the 48-hour cadence with a 12-hour scheduling grace.

Runner status is considered fresh for one hour.

## Safety

- no Docker volume pruning;
- no automatic TBankrot actions;
- no automatic source reconciliation from the dashboard;
- source probes are bounded and admin-only.
