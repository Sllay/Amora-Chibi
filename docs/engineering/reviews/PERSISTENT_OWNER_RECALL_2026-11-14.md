# Step 2 — Persistent owner recall and diagnostic boundary

Step 1 saved explicit owner requests in production (turn 067a9b10-f0ac-5d59-b777-08216993d791). The earlier automatic reflection accepted a record for turn 86fba95f-5de3-5155-a0ae-fab03f0d8ce5. Neither log alone proves later retrieval, content fidelity or remote authorization.

## Step 2 implementation

- Verified owner Discord recall requests use a local persistent-store path. No reliance on recent chat history or provider availability; exact subject terms, accent normalization, no unrelated entity fallback, bounded response. Generic recall returns recent records, not a reconstruction of a particular conversation.
- Owner-scoped active records only. FTS searches the eligible corpus; when FTS is unavailable/incomplete a bounded 512-record fallback reports incomplete search rather than claiming absence.
- Group owner requests read only PUBLIC records. A missing public match receives a neutral privacy explanation, identical whether a private record exists or not. No private IDs, content or existence indicators are projected into the group. No authorization mechanism until Step 3.
- Private local-only records remain local; retrieval does not modify remote consent. Provider context named recall also requires actual topic terms rather than unrelated entity fallback. No changes to V3 or dialogue.py.
- Operator-only command `memory recall inspect TURN_UUID [TOPIC]` reports record IDs, active/archive state, remote processing flag and bounded recall result membership; no content. It does not mutate storage or call providers.

## Production checkpoint

After deploying the Core, run:
`memory recall inspect 86fba95f-5de3-5155-a0ae-fab03f0d8ce5 Orquídea`
`memory recall inspect 067a9b10-f0ac-5d59-b777-08216993d791 Aurora`

Expected: active owner records and returned_from_turn_count >= 1 if the supplied topic matches the stored record. A false remote flag does not prevent local private recall. Test private "Amora, o que você lembra sobre o projeto Orquídea?" and verify MEMORY_RECALL_PERSISTENT_FOUND, then group with the same question should return only public memory or MEMORY_RECALL_SHARED_SCOPE_RESTRICTED. Live Turso inspection remains pending; no claim about exact production record content until then.

No migrations, resets or Discord worker changes. Explicit scope: verified owner Discord, supported recall phrases, bounded retrieval; ordinary conversation remains provider-driven.
