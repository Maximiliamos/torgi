# BAT-309: encrypted off-host PostgreSQL backup — staged, NOT activated

The production owner must approve a geographically/administratively separate
backup bucket, account and an \`age\` X25519 public recipient. No destination
or encryption key is invented in this repository. No GitHub workflow currently
runs off-host export automatically. Existing verified three-generation local
retention and isolated restore gates remain unchanged.

## Operator preparation (no changes to the running production database)

1. Provision a **separate restricted S3 backup bucket/account** (not the public
   map-tile bucket). Enable server-side encryption, object versioning,
   immutability/retention and least-privilege write+head permissions as supported.
2. Generate an \`age\` X25519 identity OFF the production host; retain the private
   key in two protected independent locations. Give production **only the
   public** \`age1...\` recipient; never commit/print/upload the private key.
3. Install the \`age\` CLI and AWS CLI, with a narrowly scoped S3 credential.
   Provide \`OFFHOST_AGE_RECIPIENT\` and \`OFFHOST_BACKUP_S3_PREFIX\` as environment
   variables; S3 access credentials remain in the operator's secret store.
4. Validate with a dry-run (reads local metadata/checksums, no uploads):

   \`\`\`powershell
   python scripts/export_verified_backup_offhost.py --backup-root 'D:\BankrotAI\dr-backups' --dry-run
   \`\`\`

5. After written approval, perform the **opt-in** transfer, supplying the
   separately provisioned S3 endpoint if needed:

   \`\`\`powershell
   python scripts/export_verified_backup_offhost.py --backup-root 'D:\BankrotAI\dr-backups' --s3-prefix 's3://OWNER-DR-BUCKET/verified/postgres' --endpoint-url 'https://s3.regru.cloud'
   \`\`\`

The script selects the newest dump whose metadata proves
\`restore_verification=passed\`, has identical restored/source schema revision,
matches its exact local path, declared size and full SHA-256 hash. It encrypts
the dump **and** its metadata locally with \`age\` and uploads only
\`*.age\` ciphertext to a unique off-host object prefix. The object is accepted
only when the S3 HEAD size matches the ciphertext. A failure leaves any
already-uploaded *encrypted* object for later inspection; plaintext uploads are
never attempted.

## Recovery acceptance (still requires a user-present drill)

1. On **replacement infrastructure**, download both encrypted objects.
2. Decrypt offline with the owner-held private identity.
3. Recalculate the dump SHA-256 and compare with the decrypted metadata.
4. Restore into **isolated PostgreSQL**, verify schema, counts and production
   application connectivity. Document measured RPO (time of latest usable
   off-host backup) and RTO (time to working replacement service).
5. Only then approve a production failover procedure and test it with a
   controlled Windows reboot/drill. Do not restore onto the live DB as a test.

The exporter is intentionally **not** claimed production-ready until an
owner-approved destination, credentials, private-key escrow and a real restore
drill have been proven. An encrypted upload alone does not prove recoverability.
