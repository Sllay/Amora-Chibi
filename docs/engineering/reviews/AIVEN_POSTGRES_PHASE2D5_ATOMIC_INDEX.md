# Aiven Phase 2D.5 — PostgreSQL 18 atomic synthetic memory/search updates

Status: isolated CI laboratory, **not a production storage adapter**.

## Motivation

Fase 2D.4 proved FTS5 BM25 numerical parity on finite invented fixtures, but a static copied posting index is insufficient: user conversations create, edit, archive and delete memories while a search may be running. A stale term index could retrieve a deleted memory or omit recent facts.

## Implemented

`core/amora/storage/postgres_atomic_index_lab.py` provides synthetic-only:
- `put_synthetic_memory`: INSERT-ONLY or compare-and-swap update with an expected `updated_at` revision and `SELECT ... FOR UPDATE` row lock, preventing silent lost edits. The record, tags, memory_search document, document length and token postings are updated **inside one PostgreSQL transaction**. FTS source document layout matches `MemoryStore.put`: content + summary and sorted tags.
- `archive_synthetic_memory`: marks a memory archived but preserves indexed terms as the canonical SQLite FTS5 does. The retrieval ACL excludes archived rows before LIMIT. Corpus statistics remain equivalent.
- `hard_delete_synthetic_memory`: deletes only after checking owner/revision; FK cascades must remove tags, search document, doc length and postings in the same transaction.
- FTS5 unicode61 term counts are produced by a **fresh throwaway in-memory SQLite instance**, intentionally only as a lab oracle. This is NOT a production tokenizer design and MUST NOT be used on live backup content.
- Fixed errors and strict DB gate reject non-loopback hosts, non-PG18, wrong DB name and unsafe laboratory inputs. This code has no Aiven DSN, file access, Core selector or knowledge of production credentials.

## Real PostgreSQL 18 CI gates

`tests/test_postgres_atomic_index_lab.py` validates:
- Create/update semantics and comparison of PostgreSQL BM25 numeric scores against SQLite after each mutation (Portuguese accents, summary and tags included).
- Insert-only duplicate, wrong-owner and stale-revision rejection without rewriting stored records or indexes.
- Deliberate failure **mid-postings write** through a synthetic CHECK constraint; verifies full rollback of changed record, tags, search document and frequencies.
- Archiving preserves FTS corpus; hard deletion cascades all indexed structures and changes corpus stats consistently.
- Concurrent PostgreSQL clients attempting edits based on the same revision: only one commit; loser must get stale-revision error.
- Reopening a new PostgreSQL connection still returns the committed search results and postings.
- External/Aiven destination rejected without executing SQL.

## Explicit certification boundary

This is **not** the production Core `SQLConnection` adapter. It does not verify all SQLite writes/leases, privacy invariants, every query family or the complete 51-table application runtime. SQLite as an FTS tokenizer is an independent oracle for CI, not an acceptable production dependency without analysis of overhead, availability and correctness.

Prior to any private import or cutover, we still need: production-compatible tokenizer contract, all PostgreSQL dialect queries and strict error mapping, transaction retry semantics, coherent live posting maintenance across every memory update path, user-identity isolation, restart/inbound messaging/Discord end-to-end, CPU/disk/connection budgets in Aiven Free, operator-approved migration of the verified snapshot, synchronization of newer writes and rollback validation.

Never change `AMORA_STORAGE_BACKEND` or the Northflank Core/PCS4C configuration based on this prototype.
