# Experimental prompt catalog

This directory stores **verbatim, versioned** prompt files for isolated provider tests. Catalog files are data, not executable instructions or the canonical Amora identity.

## Mistral Large 4

Verified path: `jailbreak/Mistral-Jailbreak.md` (uploaded by the project owner, **unchanged**).

- Original size: **20,884 UTF-8 bytes**.
- SHA-256: `18d8e61bad0863cf8103eb6b90129f1db96e38b5901599684dee800404d9027b`.
- No character normalization, Markdown reformatting or trailing newline edits.
- Preserve future revisions under distinct filenames; compare them in an isolated test before promotion.

The content file is now present in the PR branch. Integrity verified against the original on 2026-11-05: 20,884 bytes, SHA-256 as above, Git blob SHA-1 `a8aaf8519eb9bb7a4c4d18c8a685f794ec594c40`. Its contents must stay unchanged.

## NVIDIA experiment

**Design proposal only; NOT implemented or active.** Existing Mistral production behavior is unchanged. The current Dockerfile does not copy `jailbreak/` into the container runtime; packaging must be explicitly implemented and reviewed before enabling a read-only test.

```text
AMORA_MISTRAL_JAILBREAK_MODE=separate_system
AMORA_MISTRAL_JAILBREAK_PATH=/opt/amora/jailbreak/Mistral-Jailbreak.md
```

The proposed NVIDIA Mistral social test would add a **separate first system message** containing the exact file text, followed by the existing Amora Core system message and normal conversation messages. This is one stateless chat-completions request, **not** a preliminary request that magically persists between calls. The public NVIDIA model schema documents the system role, but does not prove that this hosted model accepts two system messages in one request. A provider may reject multiple system messages; do not silently merge or retry with a changed format.

The future implementation must verify the original file at runtime by SHA-256 and fail explicitly when it is missing or changed. Provider output must remain subject to Amora Core validation and privacy restrictions. No runtime injection, payload test or NVIDIA live call has been completed in this PR.
