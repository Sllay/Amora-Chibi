# Aiven staged transport diagnosis — 2026-11-19

The operator's default Aiven Job preflight confirmed a real AIVEN_CONNECTION_FAILED on 2026-11-19. This is not limited to inventory SQL: the failure occurred when opening the PostgreSQL connection. Its cause cannot be classified yet as DNS/TCP/TLS/auth, Aiven power state, or endpoint mismatch.

The Aiven Free tier may power off a service with no initial usage within a few hours. Verify the amora-database service is Running in the Aiven Console. Never paste DSNs, passwords, tokens or CA contents into logs or chat.

## Standalone operator-only diagnostic

Select a pinned Aiven Manual Job image that contains the new script, then choose a one-off custom **command** (not a custom executable/entrypoint):

    python /opt/amora/aiven_transport_diagnostic.py

Keep the persistent default CMD, /deploy/Dockerfile.aiven-preflight Dockerfile location, manual/no-CD/no-automatic-retries, non-root user and existing secret mounts unchanged. Do not execute the .py file directly; it is copied with read-only 0444 permissions.

The diagnostic validates the DSN and CA, resolves only its validated Aiven hostname, opens/closes one bounded TCP socket to the configured database port, then attempts one TLS verify-full PostgreSQL login using the existing private CA, read-only session and temporary 0600 certificate. No SQL, imports, DDL, data reads or cutover operations are performed.

Sanitized result codes:
- AIVEN_DSN_MISSING_OR_INVALID / AIVEN_CA_CERT_REQUIRED / AIVEN_CA_FILE_UNAVAILABLE: settings validation failed before network access.
- AIVEN_DIAG_DNS_FAILED: no DNS resolution; investigate Aiven's current hostname and the Job resolver/network policy.
- AIVEN_DIAG_TCP_FAILED: DNS succeeded but the Aiven PostgreSQL port could not be reached; inspect service Running state, configured port, network policies, explicit IP allowlists.
- AIVEN_DIAG_AUTHENTICATION_REJECTED, AIVEN_DIAG_AUTHORIZATION_REJECTED, AIVEN_DIAG_DATABASE_NOT_FOUND, AIVEN_DIAG_CONNECTION_LIMIT, AIVEN_DIAG_POSTGRES_NOT_READY: structured PostgreSQL SQLSTATE identified the category.
- AIVEN_DIAG_POSTGRES_HANDSHAKE_FAILED: DNS and TCP succeeded, but TLS/PG auth could not complete; cannot safely distinguish TLS from credentials without structured SQLSTATE. Inspect DSN and project-specific CA privately.
- AIVEN_DIAG_CONNECTED: TCP/TLS/Postgres login succeeded; does NOT certify data integrity, disk capacity or production readiness.

A success returns exit 0; a blocker returns exit 2. Output never includes hostnames, IPs, DSN, certificate material, raw driver errors, SQL or private records.

Do not bypass TLS, broaden Aiven firewall rules, reset credentials, alter Core/main or run a data import solely in response to a failed connectivity check.

References:
- https://aiven.io/docs/products/postgresql/concepts/pg-free-tier
- https://aiven.io/docs/platform/howto/restrict-access
- https://aiven.io/docs/products/postgresql/howto/list-code-samples
- https://northflank.com/docs/v1/application/network/configure-network-policies
