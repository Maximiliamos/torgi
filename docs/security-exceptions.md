# Security exceptions

Security exceptions are explicit, narrow and time-bounded. They never apply to runtime high/critical findings.

## GHSA-vfj7-8cjw-p6xm

- Scope: WEB development tooling only.
- Runtime production dependency audit must remain clean.
- CI permits exactly this advisory and fails on any additional high/critical advisory.
- Owner: repository owner.
- Review cadence: every weekly dependency update and before each product release.
- Exit condition: remove the exception immediately when a patched dependency chain is available.

Do not add another advisory to the CI allowlist without documenting equivalent scope, owner, review date and exit condition here.
