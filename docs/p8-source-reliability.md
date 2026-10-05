# P8 Source Reliability

P8 separates upstream/source failures from application defects and keeps successful sources running while one provider is degraded.

## Runtime behavior

- Source transport failures are classified before retry scheduling.
- DNS, connect/read timeout, TLS, HTTP 429 and HTTP 5xx failures are operational.
- Authentication/access-limited, parser/validation, coverage and internal/database failures use separate policies.
- Three consecutive operational failures open only that source circuit for 15 minutes.
- Independent operational failures on two sources inside a two-minute window open a short global source-network circuit.
- Automatic retries use bounded 1m → 3m → 5m → 15m backoff plus deterministic 0–10% jitter.
- HTTP 429 honors a numeric `Retry-After` hint when present.
- Successful recovery probes close the source circuit and queue a safe fast source retry.
- Fast recovery never reconciles missing inventory or archives lots.
- Manual `source_paused:<source>` remains stronger than automatic recovery.
- TBankrot production activation/authentication is outside P8 and is not probed.

## Home runner / VPN

The October 5 incident showed that a Windows self-hosted runner may:
1. have working TCP/443 to GitHub,
2. create a GitHub Actions session,
3. print `Listening for Jobs`,
4. but still time out acquiring a job through a VPN/Xray route.

The observed failing host was a dynamic `*.actions.githubusercontent.com` endpoint.

For the Home runner, configure Happ/Xray so the GitHub Actions control/data path can use DIRECT routing. At minimum account for:

- `github.com`
- `api.github.com`
- `codeload.github.com`
- `*.actions.githubusercontent.com`
- `results-receiver.actions.githubusercontent.com`
- `objects.githubusercontent.com`

Do not hard-code only the one regional `run-actions-...` hostname: GitHub can return another host.

The repository includes:

- `scripts/home-runner-network-diagnostics.ps1` — local, read-only diagnostics.
- `.github/workflows/home-runner-network-diagnostics.yml` — manual inline diagnostics with no marketplace actions, so it does not depend on downloading `actions/checkout` or `actions/upload-artifact`.

Local usage:

```powershell
cd C:\BankrotAI\actions-runner
C:\path\to\torgi\scripts\home-runner-network-diagnostics.ps1
```

The script reports the runner service/process state, active route/interface, likely VPN adapters, DNS, TCP/443 and HTTPS reachability. It never changes VPN, firewall or routing settings.

## P2 map retention

A production maintenance run proved that deleting tiles for five map datasets in one SQL statement can exceed PostgreSQL `statement_timeout`.

P8 changes retention to:

- retain the current dataset and at least one rollback-ready dataset;
- process one dataset at a time;
- delete map tiles by bounded primary-key chunks;
- commit every chunk;
- delete the dataset row only after its tile rows are gone.

This makes cleanup restartable. A failed run can resume from the remaining rows instead of repeating a multi-million-row transaction.

Never use `docker volume prune` as a disk recovery action on the Home host.

## Acceptance

P8 is accepted when required CI is green and production validation confirms:

- source operational failures do not create retry storms;
- healthy peers continue to refresh;
- operational failures cannot trigger destructive missing-lot reconciliation;
- circuits recover through probes;
- operations health exposes circuit/retry/network state;
- P2 retention completes without statement timeout;
- Home runner diagnostics can identify a GitHub Actions/VPN route problem.
