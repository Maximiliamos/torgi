# REG.RU emergency SSH bootstrap

The one-time password bootstrap workflow was removed from active GitHub Actions after key-only deployment access became the normal production path.

If the REG.RU host must be reprovisioned:

1. use a user-present out-of-band console or provider recovery channel;
2. install a new restricted deployment public key into root's `.ssh/authorized_keys`;
3. verify host identity/fingerprint out of band;
4. verify key-only SSH on the configured deployment port;
5. rotate the GitHub `REGRU_SSH_PRIVATE_KEY` secret if the key changed;
6. run read-only REG.RU diagnostics before any deployment.

Do not restore a permanent password-bootstrap secret merely for convenience.
