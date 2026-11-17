# Aiven Phase 2E.4 — device, session and native identity-binding parity

Status: isolated PostgreSQL 18 laboratory, **not Core production**. Builds on PR #281 voice-lease tests.

## Source-of-truth contracts

Canonical methods were inspected directly in `core/amora/storage/database.py` and the Pydantic models in `core/amora/identity/session.py` and `profiles.py`:

- `upsert_device` persists owner, trust, opaque credential **reference** (not secret), capability JSON and last-seen. `get_device` reconstructs the typed model.
- `save_session` preserves the original device/person/authentication/start fields on ID conflict; only selected timestamps, privacy context, capabilities and `closed_at` can change in the SQL statement. `close_session` acts only once; an active session is not closed and has not expired.
- `upsert_platform_identity_binding` enforces native platform/identifier-type/account-scope/actor tuple uniqueness. `resolve_platform_identity_binding` requires `enabled=1`. `has_verified_platform_binding` additionally requires the exact person ID and `operator_verified` or `platform_authenticated`, and reconstructs the canonical Pydantic binding model. Numeric Discord snowflakes are validated by that model; usernames are not an identity authority.

## Implemented and tested

`core/amora/storage/postgres_auth_sessions_lab.py` expresses the above queries as explicit PG18 SQL. Every call first enforces the strict local disposable PostgreSQL 18 gate; there is no Aiven DSN or runtime selector.

`tests/test_postgres_auth_sessions_lab.py` runs actual `AmoraDatabase` methods on canonical in-memory SQLite v18 and compares:
- Device upsert/reload, trust changes and serialized capability sets.
- Session upsert, privacy context change, expiry, idempotent close and read-after-commit/restart via the PostgreSQL adapter.
- FK integrity (unknown device, unknown binding owner).
- Exact Discord snowflake binding, account-scope separation, verified-vs-observed trust, disabled binding and rejection of mutable usernames.
- Remote PostgreSQL refusal before any SQL.

All values and IDs are invented. No real Discord account, token or secret is used or logged.

## Explicit limitations

This is not a complete authorization migration: exact binding verification alone does not cover the full service-layer authority graph, runtime trust exceptions, Web/WhatsApp identity, platform presence permissions or credential rotation. The original SQLite `save_session` upsert permits `closed_at` changes; reproducing it is **not** proof of correct session revocation under concurrent requests. End-to-end authenticated routing, cross-device limits, crash recovery, revocation and privacy disclosure must be validated before production use.

The historical PCS.4C backup and Aiven service remain untouched. No change to `AMORA_STORAGE_BACKEND` or Northflank is required.
