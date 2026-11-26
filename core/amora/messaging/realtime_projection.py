"""Realtime delivery projection: only for already-authorized snapshots.

A provider candidate is never a DeliverySnapshot. No route is enabled here.
For live previews we prefer an explicit failure to breaking fenced Markdown
or a grapheme at an arbitrary code-unit boundary.
"""
from dataclasses import dataclass

from .realtime_contract import CURSOR, DISCORD_LIMIT, DeliverySnapshot, utf16_units


@dataclass(frozen=True, slots=True)
class DeliveryBlock:
    index: int
    text: str
    sealed: bool


def project_blocks(snapshot: DeliverySnapshot) -> tuple[DeliveryBlock, ...]:
    # Reserve enough space for either terminal or writing decoration on every
    # block. Use identical boundaries regardless of lifecycle status.
    tail = " " + CURSOR if snapshot.status == "writing" else (
        "\n\n[Resposta interrompida.]" if snapshot.status == "interrupted" else "")
    limit = DISCORD_LIMIT - utf16_units("\n\n[Resposta interrompida.]")
    if utf16_units(snapshot.text) <= limit:
        return (DeliveryBlock(0, snapshot.text + tail, snapshot.status != "writing"),)
    remaining = snapshot.text
    chunks = []
    # No splitting within fenced/inline code or unusual Markdown. These turns
    # should use existing final delivery until a full Markdown-aware splitter
    # has been certified. Do not silently alter the canonical answer.
    if any(mark in remaining for mark in ("~~~", "\x60", "[", "]", "*", "_", "~", "|", "<", ">")):
        raise ValueError("REALTIME_MARKDOWN_SPLIT_UNSUPPORTED")
    while utf16_units(remaining) > limit:
        # Split only at a complete paragraph separator, never inside a word,
        # surrogate pair, grapheme cluster, mention, or Markdown code fence.
        best = 0
        start = 0
        while True:
            cut = remaining.find("\n\n", start)
            if cut < 0 or utf16_units(remaining[:cut + 2]) > limit:
                break
            if remaining[:cut].strip() and remaining[cut+2:].strip():
                best = cut + 2
            start = cut + 2
        if not best:
            raise ValueError("REALTIME_NO_SAFE_SPLIT")
        chunks.append(remaining[:best])
        remaining = remaining[best:]
        if len(chunks) >= 8:
            raise ValueError("REALTIME_BLOCK_LIMIT")
    chunks.append(remaining)
    return tuple(DeliveryBlock(i, chunk + (tail if i == len(chunks)-1 else ""),
                               i < len(chunks)-1 or snapshot.status != "writing")
                 for i, chunk in enumerate(chunks))


class CandidateAudit:
    """Shadow metrics only; cannot deliver or log candidate text."""

    def __init__(self):
        from time import perf_counter
        self.started = perf_counter()
        self.attempts = 0
        self.attempt = None
        self.sequence = 0
        self.candidates = 0
        self.invalidated = 0
        self.transport_completed = 0
        self.first_candidate_ms = None
        self.protocol_errors = 0
        self.closed = False

    def __call__(self, event):
        from time import perf_counter
        if self.closed:
            return
        if event.attempt_id != self.attempt:
            if event.seq != 1:
                self.protocol_errors += 1
                return
            self.attempts += 1
            self.attempt = event.attempt_id
            self.sequence = 0
        if event.seq != self.sequence + 1:
            self.protocol_errors += 1
            return
        self.sequence = event.seq
        if event.status == "candidate":
            self.candidates += 1
            if self.first_candidate_ms is None:
                self.first_candidate_ms = round((perf_counter()-self.started)*1000, 2)
        elif event.status == "invalidated":
            self.invalidated += 1
        elif event.status == "transport_complete":
            self.transport_completed += 1

    def finish(self):
        self.closed = True
        return {"attempts":self.attempts, "candidate_events":self.candidates,
                "invalidated_attempts":self.invalidated,
                "transport_completed":self.transport_completed,
                "first_candidate_ms":self.first_candidate_ms,
                "protocol_errors":self.protocol_errors}


def shadow_enabled():
    import os
    return os.getenv("AMORA_REALTIME_SHADOW_ENABLED","0").strip().lower() in {
        "1", "true", "yes", "on",
    }
