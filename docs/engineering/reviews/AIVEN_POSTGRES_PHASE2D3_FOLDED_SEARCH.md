# Aiven Phase 2D.3 — PostgreSQL 18 unaccent GIN candidate experiment

Status: isolated synthetic laboratory; **no production backend, no real records, no cutover**.

## Rationale

The production Amora v18 `MemoryStore` currently indexes memory FTS5 with `unicode61 remove_diacritics 2` and orders lexical hits by `bm25(memory_fts)`. Phase 2D.2 found that PostgreSQL `simple` does not find a document containing `café` when the user searches `cafe`. PostgreSQL's supported `unaccent` extension can fold accents in a dedicated text-search dictionary configuration, but it **does not guarantee identical tokenizer boundaries or BM25 scoring**.

PostgreSQL 18 and Aiven extension reference:
- https://www.postgresql.org/docs/18/unaccent.html
- https://www.postgresql.org/docs/18/textsearch-dictionaries.html
- https://aiven.io/docs/products/postgresql/reference/list-of-extensions-for-each-version
- https://www.sqlite.org/fts5.html

Aiven lists `unaccent` among supported extensions (PG 18). Actual use in the operator's Aiven Free instance has NOT been tested; the module never connects there.

## Implemented

- `postgres_fts_folded_lab.py` installs `unaccent` into **disposable PostgreSQL lab database only**. In isolated schema `amora_pg_v18_lab`, creates text search configuration `amora_folded` copied from `simple`, mapping word token classes through `public.unaccent` then `pg_catalog.simple`.
- Creates an independent GIN expression index over memory document `content || ' ' || tags` with exactly the same text-search config used by queries. Existing `simple` index is not replaced, so comparative CI remains possible.
- Queries only IDs, with owner, AUTH/SECRET, archived, expiration and remote-only filters **before** the 150-row search limit; reports only aggregate counts and result parity flags.
- The search expression accepts bounded Unicode word tokens from Amora's existing `_tokens`; joins tokens as OR, never inserts arbitrary raw tsquery syntax. Parameterized query values.
- Test fixtures are invented and created in a temporary SQLite v18 database; imported into disposable loopback PostgreSQL 18 using the synthetic importer; query sets compared against original FTS5.
- Tests include Portuguese `café/cafe`, `ação/acao`, `maçã/maca`, `São/sao`, `coração/coracao`, `Júpiter/jupiter`, both search directions, privacy filters, GIN index presence, SQL/operator rejection and live connection rejection.

## Explicit limits

1. **Candidate recall improvement != rank parity**: PostgreSQL `ts_rank` is not SQLite `bm25`. No experiment is allowed to set `fts5_bm25_parity_certified=true`; `require_certified_ranking()` remains fail-closed.
2. FTS5 tokenization and PostgreSQL parser differ in Unicode handling, hyphenation, punctuation and matching modes. Wider adversarial tests required.
3. This lab does not implement the canonical complete `MemoryStore.rank()` RRF/MMR fusion, entity relationships, topic intent, or transactional persistence.
4. No new Aiven runtime images, extensions, schemas or indexes are created on the real Aiven service. Any future operator execution of Aiven DDL requires separate explicit approval, capacity testing (Free 1 GB) and rollback.
5. Existing PRs #269–#275 remain isolated; `AMORA_STORAGE_BACKEND` and the live Core deployment are unchanged.

## Next engineering checkpoint

Certify a rank-preserving adapter or a separately approved search contract that demonstrably preserves candidate sets, tie-breaking, normalized scores and RRF/MMR final top-K under realistic Portuguese and privacy fixtures. Validate the complete Core SQL dialect and transactions before any real backup import.
