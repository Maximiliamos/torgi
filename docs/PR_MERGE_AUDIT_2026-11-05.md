# Open PR review and rollout safety — 2026-11-05

This is a read-only GitHub audit snapshot, not proof of production state.
Do not merge unrelated PRs only because they are open.

## Integration blockers and outcomes

| PR | Scope | Finding | Next safe action |
|---|---|---|---|
| #1087 | Torgi Russia recovery | MERGED to main `ec81b834`; Home post-deploy probe succeeded, validated 24 sample lots, closed source circuit, queued FAST retry | Inspect durable Celery outcome; avoid interpreting queue acknowledgement as completed import |
| #1088 | 22-source stage diagnosis + FAST fix | Active new PR; correct bounded FAST terminal gate and add isolated diagnostic artefacts | Merge only after exact head CI, then inspect Home SHA and new 22-source stage report |
| #1086 | Integrate #1080 + #1085 and 22-source safety | Was DIRTY against main; conflict resolved in PR branch with main Torgi Russia guard preserved; still DRAFT | CI, full acceptance and production safety review before merge |
| #1080 | Individual multi-lot identities | Content already carried by #1086 | After #1086 integration, close superseded PR (do not double-merge) |
| #1085 | 09:00/21:00 scheduler and map retry | Content already carried by #1086 | After #1086 integration, close superseded PR |
| #1019 | ETP exact-SHA canary | Stale draft; S5 gate failing due absent successful exact-SHA P1 data-quality baseline | Rebase/revalidate against merged source acceptance; no production enable |
| #965 | Direct ETP and map source filters | Stale broad draft; current main already contains a source filter | Compare and selectively port only outstanding verified features |
| #960 | API benchmark and GitFlic mirror | Separate P2 draft | Separate CI/review; not an ETP ingestion blocker |
| #958 | Region/date repair and GEO | Separate P1 draft | Independent current-data quality and schema migration review |
| #955 | Local diagnostics and dependencies | Separate P1/P2 draft | Check for duplicated diagnostics with #1088 |
| #932 | Map sale integrity and cadastre | Older draft | Independent acceptance and map rollback checks |
| #930 | Map UI decomposition | Older draft | UI/build review, avoid unrelated ingestion release |
| #1066 | WEB dependency update | Open dependency PR | Dedicated WEB test/security review |
| #954 | Actions dependency update | Open dependency PR | Verify pinned action SHAs and CI |
| #951 | Pydantic range update | Open dependency PR | Python and DB integration tests |
| #950 | Anthropic requirement update | Open dependency PR | Dependency audit and API contract tests |
| #949 | Python development dependency update | Open dependency PR | Locked dependency and CI review |

## Observed Home evidence on 4 Nov 2026

- 22-profile read-only catalogue inventory #41907348810: completed successfully; no DB/map writes.
- 21-profile bounded manual QA #41907348630: completed successfully; 78 candidates and 6 exact-cadastre positions. **Unverified samples, not imported or accepted.**
- Torgi Russia source-circuit recovery #41907348778: successful; 24 validated on source SSR; circuit closed; FAST Celery job queued. **Its terminal result not verified by this report.**
- S5 latest readiness #41908791255: failed; do not enable the unaccepted ETP allowlist.
- #1086 was 12/12 green at an older head, but resolving conflicts produced a new head requiring fresh CI.

## Release protocol

1. Verify exact SHA CI and Home backup/restore before any main merge/deploy.
2. Preserve `reconcile_missing=False` and no archive for unaccepted sources.
3. Run the new stage checklist; classify PASS/FAIL/BLOCKED/NOT_TESTED/N/A.
4. A two-page sample cannot prove source completeness.
5. Require independent terminal page, 2 full scans, independent detail identities and prices/statuses.
6. Staged one-source import only after approval, then PostgreSQL, journal, GEO, MapDataset/S3 and public WEB checks.
7. Never run the legacy 19-source bulk campaign without per-source acceptance.
