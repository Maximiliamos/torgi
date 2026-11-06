## Change
Describe what changes and why.

## Production impact
- [ ] No production behavior/configuration changes
- [ ] Database migration reviewed for rollout compatibility
- [ ] Map contract/revision reviewed
- [ ] Secrets/credentials are not committed
- [ ] TBankrot behavior is unchanged unless explicitly authorized
- [ ] Rollback path is documented

## Verification
- [ ] Required CI checks are green
- [ ] Runtime dependency audit is green
- [ ] Relevant production acceptance evidence is linked when this changes deployment/operations


## ETP/parser PR contract (complete if applicable)
- [ ] I inspected `docs/ETP_NISTP_CDTRF_AGENT_HANDOFF_2026-11-06.md` and current linked blockers/PR stack.
- [ ] I updated `docs/ETP_PARSER_STAGE_CHECKLIST.md`, `docs/ROADMAP.md`, and the ETP handoff, or recorded why unchanged.
- [ ] I distinguished **implemented / unit-tested / live-source validated / merged / deployed / production accepted**; no sample PASS is presented as source acceptance.
- [ ] I recorded the exact source identity, selected filters/statuses, per-lot title/price evidence, last-page and drift limitations, and linked reproducible SHA/CI/report evidence.
- [ ] I preserved all fail-closed controls (no speculative lot details, no missing-lot reconcile/archival, no unauthorized production DB, GEO, MapDataset or map writes).
- [ ] For NISTP/CDTRF: [#1115](https://github.com/Maximiliamos/torgi/issues/1115), [#1116](https://github.com/Maximiliamos/torgi/issues/1116), and source-specific acceptance limitations are rechecked before any rollout.
