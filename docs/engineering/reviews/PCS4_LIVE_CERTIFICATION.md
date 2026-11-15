# PCS.4A — live environment preflight

This release does not enable verified replica recall. It adds a bounded, one-shot diagnostic on the current Alpine image using the already deployed Hrana HTTP adapter and stdlib SQLite. No new SDK wheel, schema migration, credentials, provider request, memory access write or production backup/restore.

## Operator
1. Deploy the merged main commit to **Amora Core**. Keep all existing environment variables. Set `AMORA_PCS4_PREFLIGHT=1` on Core only, redeploy. No new secrets. Do not put database credentials in GitHub or chat.
2. Wait for the `PCS4_PREFLIGHT` log (status ok, mismatch, skipped or error). It runs on a separate thread/connection once per API lifespan. Alternatively, execute `python -m amora.storage.pcs4_probe` inside the running Core container with the flag off.
3. Copy only this numeric diagnostic line and normal `critical_prepare`, `critical_finalize`, `numes_stream_finished`, `messaging_inbound_result` plus Worker `TYPING_PROGRESS`/PERFORMANCE_TRACE. Talk normally after the diagnostic finishes. Remove the flag after one run; restart only if needed.
4. No actual memory content, prompt, token, endpoint or identity is logged. Temporary private directory is removed on normal completion/failure; abrupt container termination can leave ephemeral files until container disposal. Do not mount it as a backup or upload it.

## Scope and limits
A separate deferred read transaction copies only seven audited identity/memory/FTS tables, up to 10k rows and 4 MiB logical cell bytes, fetched in pages of 128 rows. It checks disk and cgroup v1/v2 headroom (64 MiB if measurable), uses a 25s cooperative deadline and 5s HTTP timeout, preserves canonical FTS/JSON ranking and fixed clock for two queries. The deadline is not a hard process kill; a currently running request may finish later. The primary ranking is read-only and no counters are updated. Both rank results must agree; no result is served to a user. Local rank time excludes snapshot construction, which is included in total_ms. Cgroup usage is sampled, not a peak. No load test or actual replica protocol certification.

## Promotion
The Core image is Alpine/musl. Published libsql 0.1.11 Linux wheels are manylinux/glibc; do not silently replace the image or install an old lab SDK. Native SDK/protocol qualification on the actual Turso service, real backup/restore in a private independent destination, full revision coverage and dispatch authorization, memory/CPU/disk limits and real conversation approval remain separate gates. Production uses the previous canonical backend even if this probe succeeds. No live credentials in CI.
