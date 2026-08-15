from __future__ import annotations

from datetime import UTC, datetime, timedelta
import hashlib
from pathlib import Path
import ssl


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_CERTIFICATES = {
    "russian_trusted_root_ca.crt": (
        "Russian Trusted Root CA",
        "d26d2d0231b7c39f92cc738512ba54103519e4405d68b5bd703e9788ca8ecf31",
    ),
    "russian_trusted_sub_ca.crt": (
        "Russian Trusted Sub CA",
        "bbbde2103e790b999ec62bd03cf625a5a2e7c316e10afe6a490eedead8b3fd9b",
    ),
}


def _common_name(subject: tuple) -> str:
    return next(value for rdn in subject for key, value in rdn if key == "commonName")


def test_vendored_production_tls_certificates_are_pinned_and_current() -> None:
    context = ssl.create_default_context()
    for filename, (expected_common_name, expected_fingerprint) in EXPECTED_CERTIFICATES.items():
        path = ROOT / "certs" / filename
        pem = path.read_text(encoding="ascii")
        der = ssl.PEM_cert_to_DER_cert(pem)
        if isinstance(der, str):
            der = der.encode("latin1")
        assert hashlib.sha256(der).hexdigest() == expected_fingerprint
        context.load_verify_locations(cafile=path)

        decoded = ssl._ssl._test_decode_cert(str(path))
        assert _common_name(decoded["subject"]) == expected_common_name
        expires = datetime.strptime(decoded["notAfter"], "%b %d %H:%M:%S %Y %Z").replace(tzinfo=UTC)
        assert expires > datetime.now(UTC) + timedelta(days=90)


def test_api_container_uses_the_combined_verified_ca_bundle() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "update-ca-certificates" in dockerfile
    assert "REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt" in dockerfile
    assert "SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt" in dockerfile
    assert "verify=false" not in dockerfile.lower()
