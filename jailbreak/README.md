# Experimental prompt catalog

This directory stores **verbatim, versioned** prompt files for isolated provider tests. Catalog files are data, not executable instructions or the canonical Amora identity.

## Mistral Large 4

Expected path: `jailbreak/mistral-large-4.md` (upload the user-provided file **unchanged**).

- Original size: **20,884 UTF-8 bytes**.
- SHA-256: `18d8e61bad0863cf8103eb6b90129f1db96e38b5901599684dee800404d9027b`.
- No character normalization, Markdown reformatting or trailing newline edits.
- Preserve future revisions under distinct filenames; compare them in an isolated test before promotion.

The content file is intentionally **not present yet** in this pull request: the GitHub connector declined the direct upload. Upload it manually with GitSync/GitHub, and check the SHA-256 before activating the experiment.

## NVIDIA experiment

**Design proposal only; NOT implemented or active.** Existing Mistral production behavior is unchanged.

```text
AMORA_MISTRAL_JAILBREAK_MODE=separate_system
AMORA_MISTRAL_JAILBREAK_PATH=/opt/amora/jailbreak/mistral-large-4.md
```

The proposed NVIDIA Mistral social test would add a **separate first system message** containing the exact file text, followed by the existing Amora Core system message and normal conversation messages. This is one stateless chat-completions request, **not** a preliminary request that magically persists between calls. The public NVIDIA model schema documents the system role, but does not prove that this hosted model accepts two system messages in one request. A provider may reject multiple system messages; do not silently merge or retry with a changed format.

The future implementation must verify the original file at runtime by SHA-256 and fail explicitly when it is missing or changed. Provider output must remain subject to Amora Core validation and privacy restrictions. No runtime injection, payload test or NVIDIA live call has been completed in this PR.
