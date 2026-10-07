# Signed release provenance (BAT-310)

Release assets are signed using GitHub Actions OIDC and Sigstore through a
pinned `actions/attest` revision. The signing identity is the exact release
workflow at `.github/workflows/release.yml`; signing requires no persistent
private key on the Home host or in repository secrets.

## Release gate

The accepted-release workflow first verifies exact-main SHA P1/P11,
production deploy, WEB and functional reliability gates. It then creates
the Python/WEB CycloneDX SBOMs and a SHA-256 manifest, attests all three
files, **verifies signatures and the accepted commit identity**, and only
then publishes the release assets. A failed signature or verification
prevents a new release from being created.

## Independent verification

From a checkout of the released tag and with the SBOMs and SHA256SUMS
downloaded from the matching GitHub Release:

```bash
gh attestation verify sbom-python.json --repo Maximiliamos/torgi --signer-workflow Maximiliamos/torgi/.github/workflows/release.yml
gh attestation verify sbom-web.json --repo Maximiliamos/torgi --signer-workflow Maximiliamos/torgi/.github/workflows/release.yml
gh attestation verify SHA256SUMS --repo Maximiliamos/torgi --signer-workflow Maximiliamos/torgi/.github/workflows/release.yml
sha256sum --check SHA256SUMS
```

The checksum list includes `requirements.lock` and
`WEB/package-lock.json` from the **same tag**, in addition to both SBOM
files. Verify the tag/commit of the GitHub Release and include the source
lockfiles when checking checksums. For a hard SHA binding, add
`--source-digest <accepted_release_commit_sha>` to the attestation commands.

## Scope and restrictions

- Attestation proves who built/signalled the artifact and links it to a
  commit; it does **not** certify that its code is safe.
- This signs the published SBOM and checksum evidence, not an unsigned
  desktop EXE or locally built container image. Those require build
  pipelines that produce and publish those exact artifacts.
- GitHub OIDC issues short-lived signing certificates; there is no static
  key to rotate. A future organization-issued code-signing certificate
  for a downloadable Windows binary remains an owner decision.
- The repository must remain eligible for GitHub artifact attestations;
  currently it is public. Changing visibility or permissions requires
  revalidating signing before release.
