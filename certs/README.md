# Additional production trust anchors

`torgi.gov.ru` uses the Russian Trusted CA chain, which is not included in
Mozilla/certifi. The PEM certificates in this directory are the root and
issuing CAs published by Russian government infrastructure:

- instructions: https://www.gosuslugi.ru/crt
- root: https://gu-st.ru/content/lending/russian_trusted_root_ca_pem.crt
- issuing CA: https://gu-st.ru/content/lending/russian_trusted_sub_ca_pem.crt
- current RSA 2024 issuing CA from the certificate's AIA:
  http://nuc-cdp.digital.gov.ru/cdp/subca_ssl_rsa2024.crt

Expected SHA-256 fingerprints:

- root: `d26d2d0231b7c39f92cc738512ba54103519e4405d68b5bd703e9788ca8ecf31`
- issuing CA: `bbbde2103e790b999ec62bd03cf625a5a2e7c316e10afe6a490eedead8b3fd9b`
- current RSA 2024 issuing CA: `2155785036c900dbb5f1bb2a1569c80c55595bd6bf94867a29bbddbc7d88a3f2`

The container adds these files to Debian's combined CA bundle. TLS verification
remains enabled; do not replace this with `verify=false` or an insecure download.

## Missing GlobalSign intermediate on etp-profit.ru

On 2026-11-18 the provider's leaf certificate was valid for `*.etp-profit.ru`
but its TLS server omitted GlobalSign GCC R6 AlphaSSL CA 2025. Windows could
build the chain; the production Debian client failed with `unable to get
local issuer certificate`. The intermediate was downloaded over verified
HTTPS from its issuing CA, using the leaf's Authority Information Access URL:

- https://secure.globalsign.com/cacert/gsgccr6alphasslca2025.crt
- DER SHA-256: `a883559231f8388daf35ce41c8101040ae8fd9b656434247b9475af592cc08ca`
- Issuer: GlobalSign Root CA - R6, already present in the Debian root bundle.
- Valid until 2027-05-21 00:00:00 UTC; renew/remove this workaround when the
  provider fixes its served chain or changes issuer.

Before inclusion, `openssl verify -CAfile /etc/ssl/certs/ca-certificates.crt`
validated the intermediate in the existing production container. Loading it
alongside the existing root bundle gave HTTP 200 from `/lots` with hostname
verification and CERT_REQUIRED. No new root or disabled verification is used.
