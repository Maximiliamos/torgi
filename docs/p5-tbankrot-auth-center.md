# P5 TBankrot Auth Center

## Purpose

TBankrot is an authenticated, access-limited source and is deliberately isolated from the normal four-source production reconciliation.

The production contract is:

```
4 automatic sources -> reconciliation -> GEO -> MapDataset -> REG.RU S3
TBankrot             -> explicit authenticated targeted sync only
```

A missing, expired, blocked or unavailable TBankrot session must never make the four-source production path unhealthy.

## User flow

1. STERDEZ polls `GET /api/tbankrot/status`.
2. If the protected session is valid, the source is shown as ready.
3. If TBankrot explicitly requires login, the navigation shows an attention badge and the TBankrot tab shows **Требуется авторизация**.
4. An administrator chooses **Войти в TBankrot**.
5. STERDEZ opens an isolated managed Chromium session and streams JPEG frames through the authenticated API.
6. The user enters TBankrot credentials and completes CAPTCHA manually.
7. **Проверить и продолжить** opens a separate search page in the same browser context and verifies that the real listing is no longer access-limited.
8. Only after successful verification are TBankrot cookies atomically saved.
9. STERDEZ queues exactly `source:tbankrot.ru`.
10. The normal ingestion pipeline persists data, runs duplicate handling and schedules MapDataset publication when map membership changes.

CAPTCHA is never solved, bypassed or automated by STERDEZ.

## Runtime isolation

The browser broker is the container `bankrotai-tbankrot-auth`.

It receives only:

- the private Docker network;
- a random broker token;
- the dedicated host folder `C:\ProgramData\BankrotAI\tbankrot-auth`.

It does **not** receive:

- PostgreSQL credentials;
- Redis credentials;
- REG.RU S3 access or secret keys;
- the STERDEZ user-session secret;
- the STERDEZ API service key.

The broker is not published on a host port. The API reaches it as `http://bankrotai-tbankrot-auth:18443`.

The API and Celery workers mount the TBankrot folder read-only. Only the browser broker can update the protected session.

## Protected files

- Session: `C:\ProgramData\BankrotAI\tbankrot-auth\tbankrot-cookies.json`
- Broker token: `C:\ProgramData\BankrotAI\tbankrot-broker-token.txt`

The old `C:\ProgramData\BankrotAI\tbankrot-cookies.json` is migrated on deploy when the new session file does not yet exist.

Cookie values must never be printed to Actions logs, API responses or WEB state.

## Broker controls

The browser broker accepts only:

- click;
- text input;
- a bounded allow-list of keyboard keys;
- wheel;
- reload;
- back.

There is no arbitrary navigate command. Top-level navigation is constrained to `tbankrot.ru`; third-party subframes are allowed so CAPTCHA and required page resources can render.

Browser sessions expire after 30 minutes.

## Auth verification

A session is accepted only when a real TBankrot search page provides search-result evidence and does not show:

- the blurred access-limited listing;
- the login requirement prompt;
- the login form.

Cookie-file existence alone is never proof of a working session.

The normal status path performs a cached live probe at most once every five minutes. It uses a short timeout so a slow TBankrot does not block the STERDEZ UI.

The manual sync and post-login verification use a longer live probe.

## States

### ready

Protected session exists and the latest bounded live validation confirms access.

### auth_required

TBankrot explicitly requires login or there is no protected session.

User action: administrator opens the Auth Center and signs in.

### source_unavailable

The broker is healthy, but TBankrot did not answer the bounded validation request.

User action: do not delete cookies or re-authenticate immediately. Retry later.

### broker_unavailable

The private browser broker itself cannot be reached.

Operator action: inspect `bankrotai-tbankrot-auth`, Docker network, disk and broker logs.

### syncing

An isolated TBankrot source run is queued or running.

## Automatic isolation guarantee

`_source_is_paused("tbankrot.ru")` is hard-wired to true for broad automatic refreshes.

The only supported ingestion mode for TBankrot is the explicit targeted mode:

```
source:tbankrot.ru
```

Deleting an AppSetting row cannot accidentally add TBankrot back to scheduled fast/full reconciliation.

## Health and maintenance

P5 is included in:

- Home production deploy health gate;
- Phase 3 production health;
- P2 Docker log-rotation checks;
- Production reliability CI;
- a dedicated Docker smoke that launches Chromium in a read-only container.

A release is not accepted if the broker image cannot build/start or its internal health endpoint is unreachable.

## Recovery

If authentication expires:

1. Open **TBankrot**.
2. Choose **Войти в TBankrot**.
3. Complete login/CAPTCHA manually.
4. Choose **Проверить и продолжить**.
5. Confirm the tab changes to synchronization/ready state.

If TBankrot is temporarily unavailable, retain the saved session and retry later.

If the broker container fails, restart/redeploy the application. Do not copy cookies into GitHub secrets or commit them to the repository.

## Security invariants

- Password is not persisted by STERDEZ.
- CAPTCHA is always a human action.
- Cookie values never leave server-side protected storage.
- A reader can see source state but cannot operate the browser.
- Browser control and targeted sync require STERDEZ admin privileges.
- The browser broker has no database, queue or object-store credentials.
- TBankrot failure cannot block the four-source production pipeline.
