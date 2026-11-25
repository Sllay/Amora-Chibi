"""Adaptive message decomposition (AMD), immutable source spans.

This module indexes literal evidence, not facts. Offsets are Unicode code
points (Python string offsets), end exclusive. A map never authorizes a
provider, replaces the source, or writes canonical memory.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import re


class MessageDecompositionLimit(ValueError):
    code = "AMD_RESOURCE_LIMIT"


_BOUNDARY = re.compile(r"\r?\n[ \t]*\r?\n|\r?\n|(?<=[.!?…])\s+")
_SIGNALS = {
    "negation": re.compile(r"\b(?:não|nao|nunca|nem|not|never|no)\b", re.I),
    "correction": re.compile(r"\b(?:mas|porém|porem|contudo|na verdade|corrigindo|em vez|instead|actually|however)\b", re.I),
    "decision": re.compile(r"\b(?:escolho|escolhi|decidi|vou|prefiro|quero|choose|decided|prefer)\b", re.I),
    "visual_reference": re.compile(r"\b(?:imagem|imagens|foto|fotos|print|screenshot|image|picture|anexo)\b", re.I),
    "question": re.compile(r"\?|\b(?:como|qual|quais|quanto|por que|what|how|which)\b", re.I),
    "reference": re.compile(r"\b(?:isso|esse|essa|aquele|aquela|ele|ela|dele|dela|this|that|it|they)\b", re.I),
}


@dataclass(frozen=True, slots=True)
class Span:
    start: int
    end: int


@dataclass(frozen=True, slots=True)
class EvidencePointer:
    kind: str
    span: Span
    block: int


@dataclass(frozen=True, slots=True)
class MessageMap:
    source_sha256: str
    source_chars: int
    blocks: tuple[Span, ...]
    pointers: tuple[EvidencePointer, ...]

    def verify(self, original: str) -> None:
        if len(original) != self.source_chars or _digest(original) != self.source_sha256:
            raise ValueError("AMD_SOURCE_MISMATCH")
        cursor = 0
        for block in self.blocks:
            if block.start != cursor or not block.start < block.end <= len(original):
                raise ValueError("AMD_COVERAGE_INVALID")
            cursor = block.end
        if cursor != len(original):
            raise ValueError("AMD_COVERAGE_INVALID")
        for pointer in self.pointers:
            if not 0 <= pointer.block < len(self.blocks):
                raise ValueError("AMD_POINTER_INVALID")
            block = self.blocks[pointer.block]
            if not (block.start <= pointer.span.start < block.end
                    and pointer.span.start < pointer.span.end <= len(original)):
                raise ValueError("AMD_POINTER_INVALID")

    def read(self, original: str, block: int, *, overlap_chars: int = 0) -> str:
        self.verify(original)
        if not 0 <= block < len(self.blocks) or not 0 <= overlap_chars <= 512:
            raise ValueError("AMD_READ_RANGE_INVALID")
        span = self.blocks[block]
        return original[max(0, span.start - overlap_chars):min(len(original), span.end + overlap_chars)]

    def metadata(self) -> dict:
        # Content-free counters for diagnostics, not the map itself or source hash.
        return {"source_chars": self.source_chars, "blocks": len(self.blocks),
                "pointer_counts": {kind: sum(p.kind == kind for p in self.pointers)
                                   for kind in _SIGNALS}}


def _digest(text: str) -> str:
    return sha256(text.encode("utf-8")).hexdigest()


def index_message(original: str, *, max_block_bytes: int, max_blocks: int = 32,
                  max_source_bytes: int = 262144) -> MessageMap:
    """Prefer paragraph/sentence boundaries, fall back to Unicode-safe byte cap.

    No whitespace is stripped; concatenating every block reconstructs the
    source exactly. Markers are lexical candidates, not resolved propositions.
    A bounded index refuses overflow instead of silently dropping a tail.
    """
    if not 4 <= max_block_bytes <= max_source_bytes or not 1 <= max_blocks <= 128:
        raise ValueError("AMD_INVALID_LIMITS")
    if len(original.encode("utf-8")) > max_source_bytes:
        raise MessageDecompositionLimit()
    boundaries = [match.end() for match in _BOUNDARY.finditer(original)]
    blocks = []
    start = 0
    boundary_idx = 0
    while start < len(original):
        if len(blocks) >= max_blocks:
            raise MessageDecompositionLimit()
        end = start
        used = 0
        while end < len(original):
            size = len(original[end].encode("utf-8"))
            if used + size > max_block_bytes:
                break
            used += size
            end += 1
        while boundary_idx < len(boundaries) and boundaries[boundary_idx] <= start:
            boundary_idx += 1
        if end < len(original):
            candidate = boundary_idx
            best = None
            while candidate < len(boundaries) and boundaries[candidate] <= end:
                best = boundaries[candidate]
                candidate += 1
            # Do not explode calls for a tiny early boundary.
            if best is not None and best - start >= (end - start) // 2:
                end = best
        if end <= start:
            raise MessageDecompositionLimit()
        blocks.append(Span(start, end))
        start = end
    pointers = []
    # Search full source so word boundaries remain meaningful at block edges.
    # A cross-boundary marker belongs to the block where it starts; its full
    # source span is retained for overlap reads.
    for kind, pattern in _SIGNALS.items():
        block = 0
        for match in pattern.finditer(original):
            while block < len(blocks) and blocks[block].end <= match.start():
                block += 1
            if block < len(blocks):
                pointers.append(EvidencePointer(kind, Span(match.start(), match.end()), block))
    result = MessageMap(_digest(original), len(original), tuple(blocks),
                        tuple(sorted(pointers, key=lambda p: (p.span.start, p.kind))))
    result.verify(original)
    return result


# Source-grounded provider analysis. It is intentionally independent of Core
# memory and realization; neither extraction nor consolidation may emit speech.
class AdaptiveEvidenceError(ValueError):
    code = "AMD_EVIDENCE_INVALID"


_EVIDENCE_KINDS = frozenset({
    "subject", "relation", "claim", "decision", "correction", "negation",
    "question", "visual_reference", "dependency", "reference", "other",
})
_GROUP_STATES = frozenset({"active", "superseded", "uncertain", "question"})


def bind_evidence(original, indexed, block_index, raw, *, overlap_chars=160):
    """Bind exact quoted substrings to offsets; reject hallucinated evidence.

    An extractor must report complete review of its block. This is a protocol
    assertion, not proof of semantic completeness. Deterministic marker
    coverage is additionally enforced for consequential lexical signals.
    """
    indexed.verify(original)
    if (not isinstance(raw, dict) or set(raw) != {"review_complete", "items"}
            or raw["review_complete"] is not True or not isinstance(raw["items"], list)
            or not 1 <= len(raw["items"]) <= 32):
        raise AdaptiveEvidenceError()
    block = indexed.blocks[block_index]
    lo = max(0, block.start - overlap_chars)
    hi = min(len(original), block.end + overlap_chars)
    window = original[lo:hi]
    evidence = []
    for item in raw["items"]:
        if not isinstance(item, dict) or set(item) != {"kind", "quote", "occurrence", "interpretation"}:
            raise AdaptiveEvidenceError()
        kind, quote, occurrence, interpretation = (item[k] for k in
            ("kind", "quote", "occurrence", "interpretation"))
        if (not isinstance(kind, str) or kind not in _EVIDENCE_KINDS
                or not isinstance(quote, str) or not 1 <= len(quote) <= 1200
                or type(occurrence) is not int or not 0 <= occurrence <= 64
                or not isinstance(interpretation, str) or not 1 <= len(interpretation) <= 280):
            raise AdaptiveEvidenceError()
        start = window.find(quote)
        for _ in range(occurrence):
            start = window.find(quote, start + 1) if start >= 0 else -1
        if start < 0:
            raise AdaptiveEvidenceError()
        start += lo
        end = start + len(quote)
        if not (start < block.end and end > block.start):
            raise AdaptiveEvidenceError()
        evidence.append({"block": block_index, "start": start, "end": end,
                         "kind": kind, "quote": quote, "interpretation": interpretation})
    # Do not let a summary silently remove a late question, correction,
    # negation, decision or attachment reference. These are hints, not facts.
    for pointer in indexed.pointers:
        if pointer.block == block_index and pointer.kind != "reference":
            if not any(item["start"] <= pointer.span.start and item["end"] >= pointer.span.end
                       for item in evidence):
                raise AdaptiveEvidenceError()
    return evidence


def merge_evidence(indexed, results):
    """Require a reviewed result for every block, preserving source order."""
    if len(results) != len(indexed.blocks):
        raise AdaptiveEvidenceError()
    items = []
    seen = set()
    for block, evidence in enumerate(results):
        if not evidence or any(item["block"] != block for item in evidence):
            raise AdaptiveEvidenceError()
        for item in evidence:
            key = (item["start"], item["end"], item["kind"], item["interpretation"])
            if key not in seen:
                seen.add(key)
                items.append(dict(item))
    items.sort(key=lambda item: (item["start"], item["end"], item["kind"]))
    for i, item in enumerate(items):
        item["id"] = i
    return items


def bind_consolidation(evidence, raw):
    """Require an explicit global grouping of every item, including revisions.

    Interpretations are transient hypotheses, never canonical facts. The
    original quote and its source offsets remain attached to the map.
    """
    if (not isinstance(raw, dict) or set(raw) != {"groups"}
            or not isinstance(raw["groups"], list) or not 1 <= len(raw["groups"]) <= len(evidence)):
        raise AdaptiveEvidenceError()
    ids = {item["id"] for item in evidence}
    covered = set()
    groups = []
    for group in raw["groups"]:
        if not isinstance(group, dict) or set(group) != {"ids", "state", "interpretation"}:
            raise AdaptiveEvidenceError()
        refs, state, interpretation = group["ids"], group["state"], group["interpretation"]
        if (not isinstance(refs, list) or not refs or any(type(i) is not int or i not in ids for i in refs)
                or len(set(refs)) != len(refs) or covered.intersection(refs)
                or not isinstance(state, str) or state not in _GROUP_STATES
                or not isinstance(interpretation, str) or not 1 <= len(interpretation) <= 400):
            raise AdaptiveEvidenceError()
        covered.update(refs)
        groups.append({"ids": sorted(refs), "state": state, "interpretation": interpretation})
    if covered != ids:
        raise AdaptiveEvidenceError()
    return groups
