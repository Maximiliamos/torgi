# Release and versioning policy

STERDEZ uses one product SemVer across the Python package, WEB package metadata and Windows installer metadata.

## Current line

- 0.3.0: first repository-closeout release after the 2026-10-07 P0–P12 production acceptance.
- A tag is `vMAJOR.MINOR.PATCH`.
- Release tags are created only by the accepted-release workflow after the exact SHA has green P11, P1, public WEB, functional, REG.RU and Cloudflare evidence.

## Change rules

- PATCH: backward-compatible bug/reliability fixes.
- MINOR: backward-compatible product or operational capability.
- MAJOR: intentionally incompatible user/API/data contract.

`scripts/check-repository-consistency.py` blocks version drift and runtime/dev lock drift.
GitHub Release evidence includes the accepted SHA, workflow provenance, CycloneDX SBOMs and SHA-256 checksums.

Desktop/API/WEB may have internal component identifiers, but public product releases use the single STERDEZ version.
