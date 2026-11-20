# PostgreSQL — Validation Run 1 (syntax, initial diagnosis and corrections)

**Scope:** only `feat/aiven-full-runtime-integration`. This is NOT authorization to merge to `main`, deploy, import user data, or switch Turso/Aiven.

**Evidence:** [diagnostic PR #287](https://github.com/Sllay/Amora-Chibi/pull/287), merged **into the integration branch** as `8372b9458a58371543863101b3d91ee9b7c73278`.

## Completed checks and observed results

- `python -m compileall -q core/amora tests`: **passed**, Python 3.12, within synthetic PG18 workflow.
- Full Core tests: **passed** on Python **3.11 / 3.12 / 3.13 / 3.14** (PR #287 latest head).
- Aiven PostgreSQL 18 synthetic laboratory: **passed** on real disposable PG18; 126 cases including static PostgreSQL SQL registry assembly, adapter imports and bind arity, FTS/transaction/identity/voice lab contracts, and a newly added real database bootstrap preflight.
- Pre-deploy security: **passed**.
- Cloud container contracts: **passed**.
- PostgreSQL runtime real adapter smoke: `prepare_disposable_core_v18()` → injected `synthetic_core_backend()` → `AmoraDatabase.migrate()` → schema version 18 → postings readiness → basic event read → connection lease release, **passed**.
- Existing production selector remains `sqlite|libsql`; Aiven/Turso production data not accessed.

## First defect identified and corrected

**Memory textual fallback:** `PostgresMemoryStore._text_rank()` returned an empty list immediately when BM25 produced no index hits. Canonical `MemoryStore._text_rank()` has a fallback using Unicode token overlap on the already authorized candidates. The missing fallback silently dropped valid memory hits when postings were absent/incomplete.

**Correction:** return the indexed ranking when present; otherwise compute the same `_tokens` overlap/Jaccard fallback as the original store. Added deterministic regression for matching/non-matching text without BM25 results.

The correction and regression were exercised in the passing PG18 synthetic workflow. This does not establish full BM25 numeric parity.

## Run 2 diagnostic queue — not claimed fixed

1. **Older owner memory retrieval:** `PostgresMemoryStore.recall_owner_records()` currently uses a bounded eight-page scan and does not reproduce the original FTS-first full-corpus recall. A topic only present in older records can be missed; fix the search semantics with proper privacy-scoped PG postings and run isolated older-corpus tests.
2. **Verified access on replicas:** `PostgresMemoryStore.record_verified_access()` intentionally fails closed; coherent PostgreSQL projection epochs and atomic authoritative access counters remain unimplemented.
3. **Full application lifecycle:** passing `AmoraDatabase` PG smoke proves connection and schema only. It does not prove `AmoraCore.bootstrap()`, Discord inbound/outbound queues, reflection consolidation, speech cancellation, recurrence, multiconnection races or crash recovery as an end-to-end flow.
4. **Natural-language memory quality:** compare FTS5/BM25 scores, accents, tokenization and RRF/MMR order against representative same-source synthetic corpora. Avoid counting only successful individual SQL calls.
5. **PostgreSQL schema v19, import, recovery:** the v19 sensory episode candidate is deliberately opt-in and unpromoted. No real source importer/cutover or PostgreSQL-native consistent backup/rollback has been certified.
6. **SQL dialect and ACL:** static coverage/test registry does not prove all conditional and dynamic query paths or per-principal authorization; execute them under target PG18 with multiple identity/privacy scopes.
7. **Warnings:** pytest-asyncio loop fixture scope unset, Pydantic `register` field shadows `BaseModel.register`. Nonfatal but worth evaluating during Run 3 maintenance; do not treat expected FK/unique violations in negative PostgreSQL tests as production errors.

## Gate

Run 1 is concluded as **syntax/import/static contract validation + initial diagnosis and first regression repair**. Run 2 must resolve/retest all reproducible correctness blockers and cannot be represented as complete based on a green CI status alone. Run 3 is separate performance/quality work. Any merge to `main` and any real Aiven cutover require separate operator permission.
