# Investigating a P11 soak health incident

Run [37713390115](https://github.com/Maximiliamos/torgi/actions/runs/37713390115)
failed its 30-minute read-only soak at 2026-10-08T01:47:07Z.
Public `/health/live` and `/health/ready` both failed in one sample,
while disk still had 77 GB free. The job previously swallowed both request
exceptions and threw *before* writing `p11-soak.json`, losing the key
per-sample evidence.

The P11 gate remains **strictly fail closed**: any observed live/ready failure
or low disk aborts acceptance, and no soak duration is shortened.

For each minute, the revised workflow persists samples to the Actions
artifact **before** evaluating failure. Both public checks run separately;
their exception *class names* are retained without dumping response bodies,
tokens, request headers or private infrastructure configuration.

If a soak fails, compare the last artifact sample and the last public
WEB/functional/production-health runs for the **same SHA**. Use separate
Home/REG.RU evidence to distinguish public edge transport, upstream gateway,
container unavailability and database readiness. A successful later public
smoke does not retroactively make the failed soak green; rerun the full P11
acceptance and only publish when exact-SHA gates succeed.

Follow-up work still requires direct production telemetry to diagnose whether
the original public failure was a network interruption, proxy timeout or an
actual application outage. Do not assert an unproven cause.
