# P5 — TBankrot Auth Center

TBankrot is an authenticated, isolated source. It is **paused by default** in
automatic fast/full nationwide reconciliation and can be refreshed only through
an explicit source-only run.

## Normal operator flow

1. Open **TBankrot** in STERDEZ.
2. If the source shows **Требуется авторизация**, click **Войти в TBankrot**.
3. Use the embedded protected browser session to sign in. CAPTCHA, when shown,
   is completed manually by the operator.
4. Click **Я вошёл — проверить и продолжить**.
5. STERDEZ opens a real-estate search page and refuses to accept the session if
   TBankrot still reports `access_limited` or the listing cannot be proven.
6. On success, only TBankrot cookies are written atomically to
   `/run/tbankrot-auth/cookies.json`.
7. STERDEZ immediately queues `source:tbankrot.ru`. The four normal production
   sources remain independent.

## Security contract

- Auth Center endpoints require an STERDEZ admin session.
- Login/password/CAPTCHA values are never persisted.
- Cookie values are never returned by status endpoints or rendered in the UI.
- The browser may access TBankrot and a bounded allowlist of CAPTCHA providers;
  localhost/private arbitrary navigation is not available.
- The API container mounts the auth directory read-write.
- Ingestion workers mount the same directory read-only.
- The interactive browser closes after a bounded idle period.
- A missing cookie file is a normal `requires_auth` state and never blocks
  production deployment.
- A legacy `C:\ProgramData\BankrotAI\tbankrot-cookies.json` file is migrated
  automatically when present.

## Failure states

| State | Meaning | Operator action |
| --- | --- | --- |
| `authenticated` | Saved cookies pass a live TBankrot listing probe | None / run refresh |
| `requires_auth` | Cookies are absent or TBankrot returned `access_limited` | Sign in again |
| `browser_active` | Interactive browser is open | Complete sign-in and verify |
| `unavailable` | TBankrot responded unexpectedly or is unreachable | Retry later; do not alter the four-source production loop |

The app rail shows an authorization alert for administrators while
`requires_auth` is true.

## Compatibility fallback

The older local capture scripts remain available for emergency recovery, but
they are no longer required for the normal operator workflow. The protected
cookie file produced by either path uses the same runtime contract.
