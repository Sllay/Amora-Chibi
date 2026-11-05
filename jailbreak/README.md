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

Default mode is `off`; existing Mistral production behavior is unchanged.

```text
AMORA_MISTRAL_JAILBREAK_MODE=separate_system
AMORA_MISTRAL_JAILBREAK_PATH=/opt/amora/jailbreak/mistral-large-4.md
```

When enabled, the NVIDIA Mistral social request adds a **separate first system message** containing the exact file text, followed by the existing Amora Core system message and normal conversation messages. This is one stateless chat-completions request, **not** a preliminary request that magically persists between calls. A provider may reject multiple system messages; do not silently merge or retry with a changed format.

The original file is verified at runtime by SHA-256; a missing/changed file makes the NVIDIA candidate fail explicitly, so Core may follow its existing error/fallback logic. Provider output is still subject to Amora Core validation and privacy restrictions. An API payload unit test does not certify that the NVIDIA-hosted endpoint accepts or follows this layout; live testing is still necessary.
