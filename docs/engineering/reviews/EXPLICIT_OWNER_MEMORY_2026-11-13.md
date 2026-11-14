# Explicit owner memory requests

An ordinary Discord DM continues to use the existing automatic reflection queue. A clear explicit request ("Quero que você lembre disso", "Lembre que ...", "Salva isso na memória: ...") follows a distinct, local Core path.

## Contract

- This first version is limited to Discord DMs from PERSON_BK with an enabled, verified native identity binding in the same account scope. Names and family claims grant no authority. Other participants and groups cannot force a write.
- The command must contain an identifiable statement in the current message. Ambiguous references to prior conversation ask for clarification. The parser is deliberately bounded; it is not a general natural-language intent model.
- Store the statement as an episodic user assertion, with the owner's identity, source turn and session; do not infer or establish canonical world facts. Preserve the wording rather than depending on a provider summary.
- Explicit requests bypass the relevance threshold, not the privacy or source-revision gates. AUTH/SECRET ingress remains blocked, obvious credentials are rejected, PRIVATE never goes remote. The existing AMORA_REFLECTION_REMOTE_REUSE opt-in and per-message remote consent control future provider access.
- The existing inbound generation lease guards the write in the storage transaction. A deterministic record ID makes replay after a delivery failure idempotent. Edits/deletions archive records from invalidated source turns. The same message does not enqueue automatic reflection.
- A deterministic confirmation is emitted only after the MemoryService returns a persisted record. Local-only memories receive a separate confirmation; ambiguous and denied requests never claim success. A database failure propagates to the inbound retry path.

## Verification

Test explicit Portuguese commands, negative/quoted/recollection cases, owner versus unverified and unknown sender, group isolation, persistence across restart, provider-independent storage, remote retrieval with consent, local-only isolation, storage failure, duplicate delivery and message edits. No migration, Discord worker change or new environment variable is needed. Live verification is still required after deploy.
