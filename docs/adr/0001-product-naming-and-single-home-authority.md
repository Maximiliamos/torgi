# ADR-0001: Product naming and single-home production authority

Status: accepted — 2026-10-07

## Decision

- **STERDEZ** is the canonical product/release name and public service at `sterdez.online`.
- **BankrotAI** remains the historical/internal Python/Desktop component name.
- Repository `Maximiliamos/torgi` is not renamed during this closeout because deployment integrations and automation refer to it.
- The Home Windows host is the current authoritative PostgreSQL/data origin.
- REG.RU provides public infrastructure and S3 map storage; Cloudflare provides WEB/edge routing. Neither is currently an independent database DR replica.

## Consequences

A full loss of the Home host is an accepted single-origin risk for the current small-user deployment. BAT-309 owns off-host encrypted backup generations, RPO/RTO and recovery maturity. Any future move of data authority requires a new ADR and a production acceptance cycle.
