# P11 Final Production Acceptance

P11 is the final evidence gate after P7–P10 are merged and accepted in production.

## What the gate proves

The manual workflow `.github/workflows/p11-production-acceptance.yml` runs only from `main` and checks:

- all canonical Home containers are running;
- public API live/ready endpoints;
- Phase 3 production health;
- a fresh PostgreSQL backup with isolated restore verification;
- DB current MapDataset ↔ REG.RU S3 public manifest equality;
- active lot / GEO snapshot evidence;
- source circuits and global source-network health;
- GEO operational retry state: `network_wait` must remain bounded, have non-stale retry scheduling, and must not indicate an open global/provider network circuit;
- Home runner GitHub route diagnostics;
- a bounded read-only soak (default 30 minutes).

All reports are uploaded as a 30-day evidence artifact.

## Optional restart drill

The workflow has an explicit `restart_drill=false` input.

When enabled it restarts application/workers, Redis and PostgreSQL sequentially, then requires the public API to recover. It does not restart Windows and never prunes Docker volumes.

## What stays manual

A full Windows host reboot cannot be safely initiated by the same self-hosted runner that must report the result. Keep the OS reboot as a user-present final drill:

1. complete a green P11 run without restart;
2. complete a green P11 run with the controlled container restart;
3. reboot Windows manually;
4. confirm Docker and the GitHub runner service return;
5. run P11 once more with `restart_drill=false`.

Network/VPN/DNS/TLS fault behavior is covered by P6/P8 regression tests plus the Home runner route diagnostic; P11 does not intentionally cut the host network because doing so would also sever the only control channel.

## Acceptance order

Before declaring the project production-ready:

1. successful full source reconciliation;
2. successful backup + restore drill;
3. successful P2 maintenance;
4. green production health/public smoke;
5. P9 GEO canary accepted;
6. P10 operations dashboard accepted;
7. P11 green soak and evidence artifact;
8. optional controlled container restart;
9. manual Windows reboot + final P11 rerun.

TBankrot activation remains outside this gate until explicitly authorized.
