# Aiven Phase 2F.2 — original ConversationStore reads via PostgreSQL 18

Status: **experimental READ-ONLY integration harness**; no serving Core backend. Stacked on Phase 2F.1 SQL audit.

## Why this is different from prior synthetic domain labs

Previously, PostgreSQL domain functions were called directly by isolated tests. This checkpoint instantiates the **actual existing `ConversationStore` class** with a reviewed, caller-supplied PG18 read adapter. Its canonical `get_turn_by_dedupe`, `list_turns`, `turn_count`, `get_utterance_by_dedupe`, `get_utterance`, `list_utterances`, `pending_count` and `spoken_count` methods execute *unchanged* while synthetic SQLite v18 and PostgreSQL 18 receive the same invented fixtures.

`postgres_conversation_read_lab.py` only recognizes **eight explicit reviewed source SQL strings**, mapped to explicit, schema-qualified PostgreSQL SQL. It checks argument number and type, caps list size to 200, materializes row results matching the `SQLConnection` contract, refuses all other SQL (including changing SQL text, raw SQLite placeholders, DDL) and refuses every write/transaction through this wrapper. No generalized SQL translation.

The owner/caller supplies an already connected disposable `127.0.0.1` PG18 `amora_pg_synthetic` connection. The existing local database guard checks it before every SQL statement. No DSN, environment selector, remote provider, imported backup or production process integration.

## Security and limitations

**These are not serving-safe authorization APIs.** The canonical ConversationStore list/count/get methods do not enforce per-principal ACL in SQL; they are intended to be used behind the rest of the Core authorization layer. This test wrapper cannot be connected to real user data and must not be registered as a production `StorageBackend`.

The bridge does not support writes, speech enqueue/claim/ack/cancel, schema upgrade, user identity or adaptive memory retrieval. Those remain in separate lab modules and are not unified at runtime. No result from this checkpoint authorizes changing `AMORA_STORAGE_BACKEND`.

Test cases:
- Canonical `ConversationStore.put_turn_decision` on SQLite and reviewed synthetic PG18 turn insert; compare **actual original read methods** and Pydantic models, including deduplicated results.
- Canonical SQLite enqueue and synthetic PG18 voice enqueue; compare read, listing, pending/spoken after acknowledge and read-after-reconnect.
- Explicit unknown SQL, tampered SQL, injected argument, invalid arity/type/limit and attempted write refusal.
- Reject any remote PostgreSQL connection before SQL.

The full Python 3.11–3.14 Core matrix is now enabled for stacked Aiven PRs after Phase 2F.1. CI still must pass separately; this is not an end-to-end Discord/Android test.

## Next gates

Complete a truly authorization-aware runtime interface, integrate **atomic write paths** from previous experiments without bypassing BM25 postings, audit the remaining SQL call sites (MemoryStore, AmoraDatabase, actions/autonomy/leases), test all transactions, compare v18-v19 schema/runtime source state, benchmark storage/connection limits and prove rollback before Aiven deployment.
