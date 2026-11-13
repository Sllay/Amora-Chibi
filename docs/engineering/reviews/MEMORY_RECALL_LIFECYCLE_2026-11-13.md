# Memory recall lifecycle — PR 193

## Deployment contract

This change is additive schema V18 (not previously deployed): memory reflection
jobs, revision invalidations and inbound generation leases. Use the existing
SQLite/libSQL migration and backup path. No changes to the original V3 file or
dialogue orchestrator. No production data is migrated into a wider privacy scope.

The API lifespan starts the bounded reflection executor. Normal Discord reply
generation does not await reflection. An eligible input records the enqueue
intent before queue insertion; the executor reconciles a crash between writes.
Jobs claim for 180 s, execute reflection for at most 60 s, retry with backoff
and expose failed/cancelled states. An operator can rearm failed jobs. Edits
invalidate earlier revisions; deletion invalidates the source. Old direct
reflection memories are archived rather than erased, and stale conversation
turns are excluded from provider history. An old lease cannot write a memory.

## Operator commands

All commands require the existing operator boundary. Results show counts and
identifiers, not conversation text.

- `memory reflection status`: pending/claimed/done/failed/cancelled counts and bounded failed IDs.
- `memory reflection retry TURN_UUID`: rearm one failed job.
- `memory historical audit`: bounded candidates for historical review and local-only reflection inventory.
- `memory historical recover TURN_UUID --confirm`: reprocess a single eligible, previously remote-authorized DM input with no completed reflection.
- `memory historical authorize MEMORY_UUID OWNER_ID --confirm`: enable reuse of one reviewed PERSONAL inference only when the original private source and owner match. The operator must have the owner's explicit approval for the reviewed memory. No bulk promotion, no public scope change, and no approval of a record with missing provenance or invalidated source.

## Explicit configuration

`AMORA_REFLECTION_REMOTE_REUSE=1` is an explicit operator opt-in for future
PERSONAL private-scene reflection memories, only where the original input
already authorized remote processing and a canonical owner is known. It
defaults off. Do not enable for an owner without authorization. Existing
local-only records require individual review; they do not become remote
automatically. Shared contexts still exclude nonpublic long-term memories.

`AMORA_REFLECTION_OBSERVED=1` permits known-participant CONTEXT_ONLY turns
already authorized for remote processing to enqueue a memory reflection.
Defaults off to avoid expanding background processing of group speech.
Observed-only reflections do not create hypotheses or action suggestions.

## Release gates

1. Full Python suite on supported versions; remote Hrana simulation, migration,
   backup/restore, privacy and container/security checks.
2. Deploy only after review/approval. Back up production before migration.
3. Verify schema/readiness, worker connectivity and operator queue status.
4. With synthetic non-sensitive content, verify a DM turn creates a job, the
   job completes, and an authorized memory is recalled after a Core restart.
5. Verify a distinct public conversation cannot retrieve the private marker.
6. Verify edit/delete and retry paths with synthetic fixtures. No real private
   conversation is required for certification.

The inbound generation lease prevents simultaneous ownership and stages an
outbound before delivery persistence; a retry can reuse the staged response.
A process crash before staging can still require a new provider call; an
external provider is not an exactly-once transactional participant. The
existing worker delivery and provider quotas remain separate operational gates.
