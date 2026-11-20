# Aiven Phase 2E.3 — voice queue leases / interruptions, PostgreSQL 18

Status: **isolated synthetic PostgreSQL 18 CI lab**, not a serving Core backend. No Aiven/Turso/PCS4C access, file path, DSN, deployment, or backend selector.

## Canonical source

The source behavior is taken from `core/amora/conversation/store.py` and real `SpeechUtterance` Pydantic models. It includes enqueue and dedupe, priority/device ordered claim, 30-second default lease bounded to 5–180 seconds, reclaim of expired claims, same-turn lookahead up to 32 candidates, acknowledge/requeue/spoken, cancel and speech-only cognitive-turn stop.

## Implemented

`core/amora/storage/postgres_speech_leases_lab.py` takes a caller-supplied PG18 connection, verifies it is on loopback database `amora_pg_synthetic` before any SQL, and writes the same utterance JSON format and status/lease columns in a single transaction. It supports:
- Enqueue with duplicate id/dedupe refusal; replays voice-only stop marker to prevent queued late segments from resurrecting a canceled cognitive turn.
- Expiration/requeue and priority claim with PostgreSQL row locking and `SKIP LOCKED`; independent workers cannot reserve one item twice.
- Acknowledge, cancel, and cancel-all-for-cognitive-turn with row locks and terminal state guards. Only `speech_only` cancellation carries the future-segment stop marker.
- Every state change writes both the denormalized status/lease columns and `utterance_json` atomically; SQL errors roll back the full change.
- All test text is invented. This is not a public voice or private message disclosure interface.

`tests/test_postgres_speech_leases_lab.py` executes real `ConversationStore` methods on disposable **in-memory SQLite v18** alongside PG18 for matched models and simulated clocks. Additional PG-specific tests cover two independent workers claiming one utterance, rollback on a forced claim constraint violation, and fresh-connection visibility.

## Limits / open gates

1. SQLite's single-process `RLock` model is not distributed. PostgreSQL row-locking semantics may differ in multi-device lookahead and fairness; the test preserves user-visible safety invariants, not a bit-identical scheduler under arbitrary interleavings.
2. Voice-stop cancellation races with new segment enqueue from another connection can require a dedicated turn tombstone; current cancellation scans existing canceled segments. **Do not activate PG queue for production until race coverage and durable stop markers are proven.**
3. Scanning all expired claims and queued rows is not a production-scale pagination strategy. Performance, claimed indexes and transaction isolation under Aiven limits are not yet certified.
4. No integration with Android/Web streaming, TTS, voice clones, Discord or real Cloud service. DB adapters and full Core SQL remain incomplete.
5. The historical backup remains intact. This phase never reads or imports real memory.

No Northflank/Aiven action is required.
