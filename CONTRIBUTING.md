# Contributing

STERDEZ uses protected `main` and pull-request based changes.

## Rules

1. Create a focused branch and PR; do not develop directly on `main`.
2. Keep migrations backward-safe during rollout.
3. Never weaken fail-closed production, backup/restore, map-integrity or security gates merely to make CI green.
4. Never commit credentials, production dumps, user data or auction documents.
5. If a map payload/eligibility contract changes, update the map dataset revision and acceptance evidence.
6. If production topology changes, update the operations runbook and ADR.
7. New modules over 1,000 lines require an ADR; existing oversized modules are grandfathered only while BAT-308 is being reduced.
8. Runtime and development lock files must agree on every shared package version.

The authoritative operational runbook is `docs/phase4-lite-operations.md`.
