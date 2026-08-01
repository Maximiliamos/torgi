# Security Policy

## Supported versions

Security fixes are provided for the latest released version and the current `main`
branch. Older snapshots and unmerged branches are not supported.

## Reporting a vulnerability

Do not open a public issue with exploit details, credentials, personal data, or
auction documents. Use GitHub private vulnerability reporting for this repository.
If private reporting is unavailable, contact the repository owner privately and
include:

- affected version or commit;
- reproduction steps and impact;
- whether credentials or personal data may be exposed;
- suggested remediation, if known.

You should receive an acknowledgement within 3 business days. A remediation and
disclosure timeline will be agreed after triage.

## Deployment expectations

- Production requires non-default PostgreSQL, Redis, API, and WEB credentials.
- PostgreSQL, Redis, and the API are not published directly by the supplied
  production Compose configuration.
- TLS verification must remain enabled. A custom CA may be supplied with
  `NSPD_CA_BUNDLE`; insecure mode is never permitted in production.
- AI provider secrets belong in environment variables or a secret manager, not
  in the application settings table.
- EFRSB credentials belong in `FEDRESURS_LOGIN` / `FEDRESURS_PASSWORD` secrets;
  demo or production passwords must never be committed.
- Auction documents may contain personal and banking data. Production document
  storage requires encryption, access control, malware scanning, retention and
  tested deletion/backup procedures.
- AI results are preliminary machine hypotheses and require independent human
  review.
