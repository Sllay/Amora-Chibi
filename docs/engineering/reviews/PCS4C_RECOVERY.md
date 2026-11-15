# PCS.4C — Private recovery certification

PCS.4A/B qualified a read-only snapshot and canonical ranking against the live
Core/Turso. This stage does not enable local projection or replace the Alpine
image. It qualifies recovery separately; issue #221 stays open until a real
independent restore and private durable backup are verified.

## Operator procedure

1. Confirm the deployed Core revision and arrange a maintenance window. Stop
   Discord/other ingress and all source writers, including reflection/scheduler
   and other Core instances. Run the existing backup CLI in an authorized
   one-off Core job with the production storage configuration and a private
   writable durable volume (not a job that serves HTTP). The command bootstraps
   Core; do not point it at a different or uninitialized source.

   `umask 077; python -m amora backup --name amora-pre-pcs4c.db`

   The CLI prints the database path. Preserve the matching
   `.db.avatar.zip` companion. The backup takes a consistent transaction and
   may reserve the writer; it is not streaming. Check disk/RAM and timing first.
   Use a fresh backup name; do not overwrite a previous certified backup.

2. Copy both files to private durable storage, outside the ephemeral container.
   Verify the durable copy in a private one-off job running the updated Core
   image:

   `python -m amora.storage.pcs4_recovery verify /private/amora-pre-pcs4c.db`

   The verifier checks SQLite integrity, foreign keys, current schema,
   companion manifest, archive and SQL asset hashes. It emits aggregate
   counts/sizes and SHA256 of the two backup files. Keep the manifest/report
   privately with the backup. Never upload the database, assets, or report
   hashes to a public repository or chat. Resume the source services after the
   backup is secured; the restore drill does not require source downtime.

3. Provision a NEW empty Turso database in an independent destination, with a
   distinct database-specific write token. Confirm the destination in the
   hosting dashboard; a different hostname alone cannot prove physical
   isolation. In a separate one-off job (no Core/Discord connected to that
   destination), inject:
   - AMORA_LIBSQL_URL: source origin (for comparison only; source token not needed)
   - AMORA_PCS4C_DESTINATION_URL: independent database origin
   - AMORA_PCS4C_DESTINATION_TOKEN: independent database token as a secret

   Use the same durable backup pair. Run:

   `python -m amora.storage.pcs4_recovery restore /private/amora-pre-pcs4c.db --isolated-empty-destination`

   The tool rejects equal hostnames, reused source token if present, and
   nonempty destination. It imports using the existing audited transaction,
   re-exports from a new destination connection and compares logical rows,
   including duplicates, schema, and SQLite sequence state. It does not
   compare physical page layout or unexposed FTS rowids. If a failure occurs,
   retain the isolated destination for diagnosis; never retry into a populated
   database or reset it automatically. The drill does not bootstrap Core or
   invoke providers. Export and comparison are not streaming end-to-end; use
   a separate resource budget rather than the live serving process.

4. Confirm a successful `PCS4C_RECOVERY` restore status and equal_tables == tables.
   Report only status, schema_version, tables, equal_tables, rows and assets
   to the project. Keep SHA256 and files private. Then separately validate
   a Core using the isolated destination, with providers disabled, using a
   controlled private recall/restart check (owner, remote, public, archived
   records), no external provider or Discord connection. Restore must not
   repoint live clients. Record durable storage retention, source/destination
   identity and the exact image privately.

## Limits and promotion

This is an operator-invoked tool, not a live backup job or automatic restore.
The backup is a point-in-time copy and does not include later changes. The
current-schema comparison is deliberately strict; legacy import is still
handled by the separate existing recovery utility. A successful restore
does not prove native replica synchronization or authorization freshness.
Production projection remains disabled. Do not close #221 or promote until
the real recovery, durable retention and restart/recall gates are met.


## Final restart/recall gate (operator-only)

After a successful restore, keep the same independent destination disconnected
from serving Core/Discord and mount the verified backup pair in the isolated job.
Use the same source origin and destination-specific environment variables from
step 3. Execute:

`python -m amora.storage.pcs4_restart /private/amora-pre-pcs4c.db --isolated-restored-destination`

The command first re-exports the destination and checks exact logical parity.
It then boots Core from a private SQLite copy of the backup, and boots the
independent remote Core twice in separate processes with fresh private homes
and a shared fixed clock. Each boot runs five canonical read-only ranking
queries (owner, owner+remote, public+remote, include archived, lexical).
It checks result equality, access permissions and nonempty overall coverage.
No HTTP server, scheduler, Discord or external AI inference is started;
only destination storage credentials are passed into remote child processes.
Core bootstrap itself can write initialization state in the isolated database,
so do not run it on production, and do not assume the destination remains
byte-identical afterwards. Temporary homes and private result digests are
deleted on normal exit. The source backup is not modified.

Report only `PCS4C_RESTART` status, boots, query_count, rank_equal, hit_counts,
private_hits, archived_hits, public_hits and nonremote_hits. Zero coverage for a
class means that class was not exercised by the actual sample, not that the
policy passed a positive test; use the existing synthetic test for that class,
or arrange a controlled isolated fixture for stronger real-data coverage.
Do not report fingerprints or memory text. An error or timeout is not a
certification. Keep #221 open until private durable retention and both real
restore and restart evidence have been reviewed.
