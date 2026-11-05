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

**Operator-only experiment implemented in ADR-0037, not auto-active.** The default Mistral production behavior is unchanged. The Amora Shell command explicitly loads this asset read-only after SHA-256 verification; the Dockerfile now packages it at `/opt/amora/jailbreak/Mistral-Jailbreak.md`.

```text
AMORA_MISTRAL_JAILBREAK_MODE=separate_system
AMORA_MISTRAL_JAILBREAK_PATH=/opt/amora/jailbreak/Mistral-Jailbreak.md
```

The proposed NVIDIA Mistral social test would add a **separate first system message** containing the exact file text, followed by the existing Amora Core system message and normal conversation messages. This is one stateless chat-completions request, **not** a preliminary request that magically persists between calls. A neutral live protocol test on NVIDIA accepted two system messages and also accepted one system message (HTTP 200 in both cases). This tests **message format acceptance only**; actual compliance with the archived prompt was not evaluated. Do not silently merge or retry with a changed format.

The operator-only lab verifies this file at runtime by SHA-256 and fails explicitly if missing or altered. A/B provider output remains subject to Amora Core validation; C is a diagnostic-only synthetic case with no Core authority. Production dialogue remains unchanged. The earlier neutral NVIDIA API-compatibility test succeeded, but that test did not send the archived prompt.

## Protocol validation: 2026-11-05

- GitHub Actions: https://github.com/Sllay/Amora-Chibi/actions/runs/41979316186
- Original asset: SHA-256 and size verified in the Actions runner.
- Two **neutral** `system` messages in one request: HTTP 200, SSE `stop`, two visible chars, 2.2s.
- One **neutral** `system` message: HTTP 200, SSE `stop`, two visible chars, 54.9s.
- Both requests used model `mistralai/mistral-large-4-1t-a52b` with `reasoning_effort=low`.
- This does not prove the model obeys the archived file or relaxes any hosted provider policy. No production behavior was changed.
- The probe uses GitHub secret `NVIDIA_API_KEY` and does not print it, archived content, or generated text.

## Amora Shell A/B/C (ADR-0037)

Command: `providers experiment mistral-abc --case vampiro` (or `rpg`).
Three sequential, opt-in synthetic-case requests: A (Core), B (archived prompt
plus Core), C (archived prompt only). No arbitrary user prompt, memory write or
automatic promotion. GitHub Actions secrets do not automatically populate the
Amora runtime; configure `NVIDIA_API_KEY` through its existing secret store.
**Live behavioral results are not yet established by this documentation.**
