# Aiven Phase 2D.4 — SQLite FTS5 BM25 vocabulary/postings in PostgreSQL 18

Status: **synthetic laboratory prototype only**; no production adapter or live data.

## Goal

The Phase 2D.3 PostgreSQL `unaccent` dictionary improves Portuguese accent-insensitive candidate retrieval, but PostgreSQL `ts_rank` is not a drop-in replacement for the canonical FTS5 `bm25(memory_fts)` ranking. A false replacement could change the order of memories despite preserving all records.

SQLite documents the FTS5 `fts5vocab(...,'instance')` table for exact indexed term occurrence/positions, and its default BM25 parameters `k1=1.2, b=0.75`, negative score sorting and corpus IDF:

- https://www.sqlite.org/fts5.html

## Implemented isolated experiment

`core/amora/storage/postgres_fts_bm25_lab.py`:

1. Rejects any non-local/non-PG18/non-`amora_pg_synthetic` PostgreSQL destination, and refuses SQLite file-backed sources. Synthetic fixtures only.
2. Creates two isolated tables inside `amora_pg_v18_lab`: `fts_doc_tokens` holding per-document token lengths and `fts_term_postings` holding exact SQLite unicode61 normalized tokens and frequencies, with indexed term lookup and FK cascading deletes.
3. Extracts exact postings from the **in-memory** SQLite FTS5 vocabulary. This avoids guessing how FTS5 normalizes `café`, `ação`, and other accented terms.
4. Computes FTS5 BM25 in PostgreSQL with the same IDF and document-length normalization. Corpus statistics cover all indexed FTS5 rows, even those inaccessible under a specific privacy projection; privacy filters then apply to the results before the bounded top-150 limit.
5. Query terms are normalized by a **new disposable SQLite in-memory** FTS5 index; no user text or UUID is logged. Results compare lexical order and, in tests only, numerical BM25 values to the canonical SQLite BM25.
6. Reuses the original `MemoryStore._add_rrf` and `MemoryStore._mmr_select` static methods on synthetic input to show the existing Python rank fusion need not be rewritten if lexical ordering is preserved.

The tests exercise Portuguese/accent folds, multiple term OR, repeated tokens, original tag tokens, global corpus IDF, owner and secret/archive/remote gates, numerical scores, and fail-closed behavior. No Aiven database, historical backup, user memory, or production Core code is altered.

## Hard release gates still open

- The synthetic index is built from the pre-existing SQLite FTS5 index. A production PostgreSQL backend would need **atomic incremental maintenance** of posting lists and document stats for create/update/delete/archive, concurrent writers, restarts and crash recovery; this prototype has none.
- Reproducing numerical BM25 on finite invented fixtures is not certification of all Unicode tokenization modes, ties, query syntax, positions/phrases, offsets or full Core RRF/MMR semantics.
- `fts5vocab` is only used against a synthetic in-memory source; no operator should run this module directly on the retained PCS.4C snapshot.
- Storage overhead of postings and CPU at production scale must be measured under Aiven Free 1 GB and 20-connection constraints.
- Complete PostgreSQL Core query dialect, leases, schema migration, operator-approved private import, restart, reconciliation of later Turso writes and rollback are still pending.

**Never mark the Core storage switched or BM25 production parity certified from this laboratory.** The project remains on SQLite/libSQL for serving requests.
