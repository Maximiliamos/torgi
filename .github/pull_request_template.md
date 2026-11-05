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


## ETP / parser changes only (mark N/A for unrelated PRs)
- [ ] Read `AGENTS.md` and `docs/ETP_PARSER_STAGE_CHECKLIST.md`.
- [ ] Identify source IDs, parser stage, exact revision SHA and source-level first failure.
- [ ] Compare official GET/POST/filters/pagination contract against evidence, including lot vs trade ID.
- [ ] Prove independent per-lot starting price, deposit, status, cadastral and other owned fields, or mark NOT_TESTED.
- [ ] Run targeted regression tests and cite actual source/CI evidence. State whether terminal page and two full read-only passes were proven.
- [ ] Report D0 diagnostics, D1 source acceptance, D2 data pipeline and D3 public map/schedules **separately**. Never treat 1–2 page sample as source approval.
- [ ] Confirm that incomplete/blocked scans cannot archive, reconcile missing lots, or enable unaccepted production sources.
- [ ] Record explicitly: DB writes, GEO/S3/map changes, deployment status and remaining FAIL/BLOCKED/NOT_TESTED stages.
- [ ] If not applicable to this PR, state: **ETP checklist N/A — no parser, catalogue, lot, ingestion, source, GEO or map change**.
