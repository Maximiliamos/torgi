"""Supply-chain regression checks: no release without signed provenance."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RELEASE = ROOT / ".github" / "workflows" / "release.yml"


def test_release_artifacts_require_sigstore_and_exact_sha_identity() -> None:
    content = RELEASE.read_text(encoding="utf-8")
    assert 'if [ "$EXISTING_TARGET" != "$ACCEPTED_SHA" ]; then' in content
    assert "Bump the version." in content
    assert "id-token: write" in content
    assert "attestations: write" in content
    assert "artifact-metadata: write" in content
    assert "Attest release evidence with GitHub OIDC and Sigstore" in content
    assert "actions/attest@59d89421af93a897026c735860bf21b6eb4f7b26" in content
    assert "Verify signed release evidence against accepted commit" in content
    assert 'gh attestation verify "$artifact"' in content
    assert '--source-digest "$ACCEPTED_SHA"' in content
    assert '--signer-workflow "$GITHUB_REPOSITORY/.github/workflows/release.yml"' in content
    assert content.index("Verify signed release evidence against accepted commit") < content.index(
        "Create accepted GitHub Release"
    )
    assert content.index("Wait for exact-SHA production gates") < content.index(
        "Attest release evidence with GitHub OIDC and Sigstore"
    )
