# Aiven manual inventory: sanitized error-stage diagnostics

A manual Northflank run of `python /opt/amora/aiven_capacity_inventory.py` returned exit 2 with `AIVEN_CONNECTION_FAILED` on 2026-11-19. This did **not** authorize or perform import or cutover. In the previous implementation, the shared connection context wrapped both `connect()` and the entire `yield` body in one generic exception handler, hiding whether the failure was TLS/auth/network or a later PostgreSQL query.

## Correction

- `AIVEN_CONNECTION_FAILED` now means the psycopg connection call itself failed (DNS, TCP, TLS, authentication, service not ready, etc.); the underlying reason is **not** revealed.
- `AIVEN_READONLY_OPERATION_FAILED` means the connection had been established but an unexpected cursor/context operation failed.
- The seven static metadata queries each return a dedicated allowlisted `AIVEN_INVENTORY_*_QUERY_FAILED` reason, without raw SQLSTATE, error strings, hostnames, DSN, credentials, query result values or table content.
- Invalid metadata or unsafe readonly/session state still blocks. Output remains a sanitized JSON record with no import/cutover approval.

## Operator follow-up, no changed secrets

1. Build/select a pinned Aiven Job image containing this correction; the Job must remain manual with CD disabled, default CMD and existing Aiven DSN/CA secrets unchanged.
2. If uncertain that the original connection works, first execute the **default** read-only preflight `python /opt/amora/aiven_preflight.py` from the same image and inspect its sanitized result. This issues only two `SHOW` statements.
3. Execute a separate **one-off custom-command override** `python /opt/amora/aiven_capacity_inventory.py` without permanently changing the Job CMD. Share only its sanitized JSON reason or counters.
4. If `AIVEN_CONNECTION_FAILED`, investigate Aiven service running state, network accessibility, secret-mount presence, DSN/CA *validity without exposing values*. If `AIVEN_INVENTORY_DATABASE_SIZE_QUERY_FAILED`, investigate role permissions on the specific function instead; do not reset credentials or weaken TLS.
5. Even a green result does not prove remaining Free storage, real snapshot freshness, external recovery, or PostgreSQL Core release readiness.

No Core/Discord/Turso/main changes, live Aiven writes, private data access, or new scheduled work are part of this change.
