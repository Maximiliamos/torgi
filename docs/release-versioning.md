# Release and versioning policy

STERDEZ uses one product SemVer across the Python package, WEB package metadata and Windows installer metadata.

## Current line

- 0.3.0: first repository-closeout release after the 2026-10-07 P0–P12 production acceptance.
- A tag is `vMAJOR.MINOR.PATCH`.
- Release tags are created only by the accepted-release workflow after the exact SHA has green Production reliability, Home deployment, P1 and P11 evidence. Public REG.RU/Cloudflare/WEB/functional evidence must be green on the same SHA, or on a proven ancestor when every intervening change is outside the deploy-sensitive path set.

## Change rules

- PATCH: backward-compatible bug/reliability fixes.
- MINOR: backward-compatible product or operational capability.
- MAJOR: intentionally incompatible user/API/data contract.

`scripts/check-repository-consistency.py` blocks version drift and runtime/dev lock drift.
GitHub Release evidence includes the accepted SHA, workflow provenance, CycloneDX SBOMs and SHA-256 checksums.

Desktop/API/WEB may have internal component identifiers, but public product releases use the single STERDEZ version.

## Public deployment provenance

Repository-only changes (for example governance, documentation, CI and acceptance workflow maintenance) do not force a redundant public WEB/edge deployment. The release workflow may reuse the latest common successful REG.RU + Cloudflare deployment only when GitHub's compare API proves that no intervening file touches the production deploy surface (`Dockerfile`, runtime/version metadata, migrations, `src/**`, `WEB/**`, edge/API proxy code or the deploy workflows themselves). Public WEB smoke and functional reliability must also be green for that ancestor deployment SHA.

This is a provenance optimization, not a gate relaxation: any deploy-sensitive change requires same-SHA public deployment evidence.
