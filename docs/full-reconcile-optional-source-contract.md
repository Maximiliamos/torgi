# Optional source coverage in full reconciliation

**Production incident, 8 October 2026:** source reconciliation run
[37713390153](https://github.com/Maximiliamos/torgi/actions/runs/37713390153)
failed at its last PowerShell guard with "Torgi Russia production parser
returned no lots". Its actual durable run reported three configured, successful
sources: `torgi.gov.ru`, `lot-online.ru`, and `bidexpert.ru`. There was
**no** `torgi-russia.ru` source in the configured set.

This is **not** evidence that `torgi-russia.ru` returned zero records. It was
not scheduled for that run, so the old unconditional extra guard was incorrect.

The workflow now:
- Reads the effective approved, unpaused source set **from the durable run**.
- Requires exactly that set of source rows, all `success` and `complete`.
- If `torgi-russia.ru` is *included*, requires a corresponding complete
  successful row and **positive** `items_seen`.
- If it is *not included*, reports it as unconfigured and explicitly makes
  **no coverage claim** for that source.
- Continues enforcing TBankrot's approved pause, MapDataset publication and
  REG.RU S3 manifest correctness.

Do **not** add `torgi-russia.ru` to full ingestion without owner-approved
source activation and live public-parser acceptance. Paused and not ingested
does not mean that a source is fixed or up-to-date.

A recovery pass should prove the exact source set and a matching accepted
GitHub SHA with a green new workflow run. Rerunning an old failed run does not
load newly merged workflow code and must not be represented as a pass.

## Production load isolation during release acceptance

P11 `37713390115` failed its public-health soak while regional ingestion was
also active on the Home host. That temporal overlap does **not** prove which
component caused the outage, but it creates avoidable contention. On push,
the GitHub-hosted `wait-home-deploy` job now waits for **both P1 and P11** to reach
**terminal** states for the same SHA before it queues heavy ingestion. The
wait is bounded and fails closed if no P11 completion is observed.

Importantly, even a failed P1/P11 can be terminal for this *scheduling*
purpose, but it remains FAILED for the separate exact-SHA release gates. There is no
shortcut to acceptance; this only avoids running full ingestion during the
hard uptime soak. Manual `workflow_dispatch` remains available for deliberate
operator reconciliation and is not delayed by a push-specific release gate.
