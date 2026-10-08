# Release when production P1 finishes after P11 (0.3.3+)

Production acceptance is strict: **both** P1 and P11 must be green for the
**identical latest main SHA**, along with production reliability, Home deploy
dependencies, REG.RU, Cloudflare, public WEB smoke and functional reliability.

Historically, P11 could pass while a long Home full reconciliation kept the P1
audit queued. The accepted-release workflow then timed out after 30 minutes,
despite no failed validation. From this release, successful completion of
**either** P1 or P11 starts the same exact-SHA gate verification. A later P1
success therefore creates a second opportunity to publish, without operator
intervention and without accepting an unverified production SHA.

Safety requirements:

- Only successful, push-triggered workflows on `main` qualify.
- The event's SHA must still equal the current `main` commit; otherwise the
  accepted-release job becomes a no-op.
- The release job checks all seven required exact-SHA gates and errors if any
  gate failed or remains incomplete beyond the bounded deadline.
- `accepted-release` concurrency is serialized and `vX.Y.Z` tags must
  match the exact SHA. Do **not** bump the version by overwriting existing tags.
- No deployment, runner job, reconciliation, P1, P11 or data check is skipped.

For **v0.3.2**, whose workflow triggers only from P11, the operator must
wait for P1 SUCCESS and then use GitHub Actions **Re-run failed jobs** on
the release run, verifying its accepted SHA is still current. Do not publish
manually or bypass the mandatory gates.
