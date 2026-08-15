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
