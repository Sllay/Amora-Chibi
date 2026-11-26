"""Bounded, incremental *candidate* extraction from structured realization.

No candidate is authorized for delivery by this parser. Full realization,
source/lease, causal and surface checks remain separate. No logs or storage.
"""
from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, field
import json
from typing import Callable, Literal

from .realization import RealizationMove

MAX_WIRE_CHARS = 65536
MAX_MOVES = 5
_KEYS = {"moves", "closing", "self_choices"}


class CandidateError(ValueError):
    """Only a fixed reason; never include provider text."""


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise CandidateError("DUPLICATE_JSON_KEY")
        result[key] = value
    return result


def _decode(raw):
    return json.loads(raw, object_pairs_hook=_unique_pairs,
                      parse_constant=lambda _: _invalid_constant())


def _invalid_constant():
    raise CandidateError("NON_JSON_CONSTANT")


@dataclass(frozen=True, slots=True)
class CandidateEvent:
    attempt_id: str
    seq: int
    status: Literal["candidate", "transport_complete", "invalidated"]
    # Do not accidentally print private content in a debug repr.
    text: str = field(default="", repr=False)


CANDIDATE_SINK: ContextVar[Callable[[CandidateEvent], None] | None] = ContextVar(
    "amora_realtime_candidate_sink", default=None
)


class MoveExtractor:
    """Single pass over chunks; emits only completed, schema-shaped moves.

    Accepts arbitrary top-level key order and arbitrary SSE chunk boundaries.
    Non-speech root members never enter emitted text. Strict duplicate-key
    rejection and final comparison invalidate candidate streams that diverge.
    """

    def __init__(self, allowed_moves):
        self.allowed = frozenset(allowed_moves)
        self.state = "root"
        self.buffer = ""
        self.pos = 0
        self.scan = None
        self.keys = set()
        self.key = None
        self.moves = []
        self.pending = None
        self.invalid = False

    def _fail(self):
        self.invalid = True
        self.pending = None
        raise CandidateError("INVALID_CANDIDATE_STREAM")

    def _skip_ws(self):
        while self.pos < len(self.buffer) and self.buffer[self.pos] in " \t\r\n":
            self.pos += 1

    def _value(self, starts):
        # A resumed scanner has already checked the initial token.
        if self.scan is None:
            self._skip_ws()
            if self.pos == len(self.buffer):
                return None
            ch = self.buffer[self.pos]
            if ch not in starts:
                self._fail()
            self.scan = [self.pos, self.pos + 1, [] if ch == '"' else ['}' if ch == '{' else ']'],
                         ch == '"', False]
        start, cursor, stack, quoted, escape = self.scan
        while cursor < len(self.buffer):
            ch = self.buffer[cursor]
            cursor += 1
            if quoted:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    quoted = False
                    if not stack:
                        break
            elif ch == '"':
                quoted = True
            elif ch in "{[":
                stack.append("}" if ch == "{" else "]")
                if len(stack) > 8:
                    self._fail()
            elif ch in "}]":
                if not stack or stack.pop() != ch:
                    self._fail()
                if not stack:
                    break
        else:
            self.scan = [start, cursor, stack, quoted, escape]
            return None
        self.scan = None
        self.pos = cursor
        try:
            return _decode(self.buffer[start:cursor])
        except (ValueError, TypeError, RecursionError):
            self._fail()

    def feed(self, chunk: str) -> tuple[str, ...]:
        if self.invalid:
            raise CandidateError("INVALID_CANDIDATE_STREAM")
        if not isinstance(chunk, str) or len(self.buffer) + len(chunk) > MAX_WIRE_CHARS:
            self._fail()
        self.buffer += chunk
        emitted = []
        while True:
            # Never skip inside a partially scanned JSON string/value.
            if self.scan is None:
                self._skip_ws()
            if self.pos == len(self.buffer):
                break
            ch = self.buffer[self.pos]
            if self.state == "root":
                if ch != "{":
                    self._fail()
                self.pos += 1
                self.state = "key"
            elif self.state == "key":
                key = self._value('"')
                if key is None:
                    break
                if key not in _KEYS or key in self.keys:
                    self._fail()
                self.keys.add(key)
                self.key = key
                self.state = "colon"
            elif self.state == "colon":
                if ch != ":":
                    self._fail()
                self.pos += 1
                self.state = "value"
            elif self.state == "value":
                if self.key == "moves":
                    if ch != "[":
                        self._fail()
                    self.pos += 1
                    self.state = "move"
                else:
                    value = self._value('"' if self.key == "closing" else "[")
                    if value is None:
                        break
                    if self.key == "closing" and value not in {"none", "statement", "question"}:
                        self._fail()
                    self.state = "member_end"
            elif self.state == "move":
                if len(self.moves) >= MAX_MOVES:
                    self._fail()
                value = self._value("{")
                if value is None:
                    break
                try:
                    move = RealizationMove.model_validate(value, strict=True)
                    if (move.kind not in self.allowed or move.kind == "stay_silent"
                            or not move.text.strip() or move.text.strip() == "SILENCE"):
                        self._fail()
                    move.text.encode("utf-8", errors="strict")
                    self.pending = move.text.strip()
                except (ValueError, TypeError, UnicodeError):
                    self._fail()
                self.state = "move_end"
            elif self.state == "move_end":
                if ch not in ",]":
                    self._fail()
                self.pos += 1
                self.moves.append(self.pending)
                self.pending = None
                emitted.append(" ".join(self.moves))
                self.state = "move" if ch == "," else "member_end"
            elif self.state == "member_end":
                self.pos += 1
                if ch == ",":
                    self.state = "key"
                elif ch == "}" and self.moves:
                    self.state = "done"
                else:
                    self._fail()
            elif self.state == "done":
                self._fail()
            else:
                self._fail()
        return tuple(emitted)

    def finish(self, parsed):
        if self.invalid or self.state != "done" or self.scan is not None:
            self._fail()
        # Verify exactly the strict object parsed from the same bytes.
        try:
            if _decode(self.buffer) != parsed:
                self._fail()
            expected = [move["text"].strip() for move in parsed["moves"]]
            if expected != self.moves:
                self._fail()
        except (ValueError, KeyError, TypeError, AttributeError):
            self._fail()


class CandidateEmitter:
    """Attempt-local, best-effort channel to an explicitly bound internal sink."""

    def __init__(self, sink, attempt_id, allowed_moves):
        self.sink = sink
        self.attempt_id = attempt_id
        self.extractor = MoveExtractor(allowed_moves)
        self.seq = 0
        self.closed = False

    def _emit(self, status, text=""):
        self.seq += 1
        try:
            self.sink(CandidateEvent(self.attempt_id, self.seq, status, text))
        except Exception:
            # Failure of optional observation cannot affect provider output.
            self.closed = True

    def feed(self, chunk):
        if self.closed:
            return
        try:
            for text in self.extractor.feed(chunk):
                self._emit("candidate", text)
                if self.closed:
                    break
        except CandidateError:
            self.close(False)

    def complete(self, parsed):
        if self.closed:
            return
        try:
            self.extractor.finish(parsed)
        except CandidateError:
            self.close(False)
            return
        self.close(True)

    def close(self, completed=False):
        if not self.closed:
            self._emit("transport_complete" if completed else "invalidated")
            self.closed = True
