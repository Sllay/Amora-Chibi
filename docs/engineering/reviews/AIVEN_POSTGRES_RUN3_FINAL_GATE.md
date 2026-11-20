# Aiven PostgreSQL — Run 3 final hardening and integration gate

**State: IN PROGRESS.** Run 2 candidate correctness was completed in PR #289;
Run 3 is the final pre-integration *development* audit. The production Amora
cutover is a separate operator-controlled acceptance, not an implied result.

- Base branch: `feat/aiven-full-runtime-integration`
- Work branch: `feat/aiven-run3-final-hardening-20261118`
- Source repository: `Sllay/Amora-Chibi`
- No `main` merge, production Aiven connection or source SQLite/Turso
  private data import is part of this run.

## Run 3 checkpoints

| Gate | Required evidence | Status |
|---|---|---|
| R3.1 — structural preflight | Read-only repeatable-read PG18 snapshot validates exact canonical v18 tables, columns and types, migrations, FTS index, validated constraints, optional memory projection epoch, and exact target policy | Implemented; CI pending |
| R3.2 — lifecycle reliability | Repeated `AmoraCore.bootstrap()` / close, synthetic usage persistence after restart, zero database connection leaks, bootstrap failure injection with clean retry | Implemented; CI pending |
| R3.2a — offline source gate | Operator-only PCS.4C v18 + sidecar verification; mocked/constructed CI file exercises drift, tamper and redaction; no private source ever sent to CI | Code integrated; synthetic CI pending; real source verification separate |
| R3.2b — concurrent queue soak | Real disposable PG18, 96 synthetic outputs, 4 synchronized workers, idempotent claim/ACK, restart and bounded connection budget | Code integrated; CI pending |
| R3.3 — final regression matrix | Disposable PG18 real backend, Core Python 3.11–3.14 (including Python provider/capability contracts), security, cloud container, PostgreSQL worker/voice/cognition and perceptual-memory checks on one immutable PR SHA | Pending |
| R3.4 — integration decision | Green checks for the consolidated #290 + #291 scope; review diff and report; merge into integration branch `feat/aiven-full-runtime-integration` only | Pending |

The `inspect_prepared_postgres` function checks an already connected target
provided explicitly by an operator. It performs **no writes** under a
`REPEATABLE READ READ ONLY` transaction, never loads credentials, never
connects automatically, and never returns private row content. A successful
report is **`structural_pass`**, with `production_ready=False` even when
all structural gates pass. An absent revision epoch, missing FTS index, changed
canonical column types, version mismatch, failed TLS/host verification or
failed query always blocks it. A live target must not be certified merely by
having a passing schema.

## Separate real Amora acceptance AFTER Run 3 (not automated in this PR)

1. Operator verifies actual Aiven PostgreSQL target, CA chain with
   `sslmode=verify-full`, readonly role and exact expected database/schema.
   Never paste credentials into a PR or workflow logs.
2. Freeze/freshly snapshot the *real* SQLite/Turso source with consistent
   transaction boundaries; reconcile every table, row, FTS document,
   attachment/blob, private scope and ownership reference with target
   under a reviewed import procedure. The synthetic 2,400-row exercise is
   **not** a production importer.
3. Prove Aiven's actual backups/PITR and a separate restoration + rollback
   drill, including projection epoch rotation before replica traffic.
4. Test the deployed Amora Core and external Discord transport with a
   verified account and controlled messages. Validate identity, cognition,
   memory recall, ACL, voice interruption, outgoing dedupe, edit/revoke,
   restart and safe fallback status with auditable sanitized logs.
5. Only after independent approval may a follow-up PR introduce a guarded
   production backend selector and deployment. A candidate injected through
   synthetic tests is NOT a production selector.

**Safety invariant:** `AMORA_STORAGE_BACKEND` remains restricted to existing
`sqlite` and `libsql` until real cutover signoff. Run 3 must not make Aiven
production active or silently replace the current Amora.
