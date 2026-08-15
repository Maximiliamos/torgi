# Additional production trust anchors

`torgi.gov.ru` uses the Russian Trusted CA chain, which is not included in
Mozilla/certifi. The two PEM certificates in this directory are the root and
issuing CA published by the Russian government certificate portal:

- instructions: https://www.gosuslugi.ru/crt
- root: https://gu-st.ru/content/lending/russian_trusted_root_ca_pem.crt
- issuing CA: https://gu-st.ru/content/lending/russian_trusted_sub_ca_pem.crt

Expected SHA-256 fingerprints:

- root: `d26d2d0231b7c39f92cc738512ba54103519e4405d68b5bd703e9788ca8ecf31`
- issuing CA: `bbbde2103e790b999ec62bd03cf625a5a2e7c316e10afe6a490eedead8b3fd9b`

The container adds these files to Debian's combined CA bundle. TLS verification
remains enabled; do not replace this with `verify=false` or an insecure download.
