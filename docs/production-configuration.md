# Production configuration ownership

The current deployment intentionally keeps sensitive values in GitHub/host secret storage and non-secret topology in version-controlled configuration.

## Canonical topology

- public WEB: `https://sterdez.online`
- public API: `https://api.sterdez.online`
- Home Windows host: authoritative PostgreSQL/data origin
- REG.RU: public infrastructure and S3 map storage
- Cloudflare: public WEB/edge routing

Provider-specific addresses and identifiers that still appear in workflow/Wrangler configuration are **non-secret deployment coordinates**, not credentials. They must be changed through a reviewed PR together with the relevant runbook/ADR and an exact-SHA production acceptance cycle.

## Migration target

BAT-310 tracks moving repeated non-secret deployment coordinates to a single reviewed configuration source where GitHub Actions and Cloudflare tooling can consume them without weakening reproducibility. Secret names are based on purpose, not a retired provider; legacy secret names should be removed only after their replacements are provisioned and verified.
