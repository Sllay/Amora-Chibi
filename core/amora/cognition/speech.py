from __future__ import annotations

import re
from dataclasses import dataclass, field


_MARKDOWN_RE = re.compile(r"(?:\*\*|__|~~|`|#{1,6}\s*)")
_STAGE_RE = re.compile(r"\((?:risos?|rindo|suspira|suspiro|pausa|sorri|sorrindo|ri)\)", re.IGNORECASE)
_QUOTE_RE = re.compile(r"[\"“”„‟«»]")
_KKK_RE = re.compile(r"\b(?:k{3,}|rsrs+|haha+|hehe+)\b", re.IGNORECASE)
_EMOJI_RE = re.compile(
    "["
    "\U0001F300-\U0001FAFF"
    "\U00002700-\U000027BF"
    "\U00002600-\U000026FF"
    "\U0000FE0F"
    "]+",
    flags=re.UNICODE,
)
_SPACE_RE = re.compile(r"\s+")


@dataclass(frozen=True, slots=True)
class SpeechRender:
    display_text: str
    speech_text: str
    nonverbal_cues: tuple[str, ...] = ()


class SpeechNormalizer:
    """Turns display text into text that a literal TTS engine can safely speak.

    It intentionally removes typography/emoji instead of making engines pronounce
    things like 'abrir aspas', markdown markers or emoji names. Non-verbal stage cues
    are returned separately so a richer voice/avatar layer can render them later.
    """

    def normalize(self, text: str) -> SpeechRender:
        display = text.strip()
        cues: list[str] = []
        for match in _STAGE_RE.finditer(display):
            cue = match.group(0).strip("()").lower()
            cues.append("soft_laugh" if cue.startswith(("ri", "rindo", "ris")) else cue)
        speech = _STAGE_RE.sub(" ", display)
        if _KKK_RE.search(speech):
            cues.append("soft_laugh")
            speech = _KKK_RE.sub(" ", speech)
        speech = _MARKDOWN_RE.sub("", speech)
        speech = _QUOTE_RE.sub("", speech)
        speech = _EMOJI_RE.sub(" ", speech)
        speech = speech.replace("—", ", ").replace("–", ", ")
        speech = _SPACE_RE.sub(" ", speech).strip()
        speech = re.sub(r"\s+([,.!?;:])", r"\1", speech)
        speech = re.sub(r"([,.!?;:]){2,}", lambda m: m.group(0)[0], speech)
        return SpeechRender(display_text=display, speech_text=speech, nonverbal_cues=tuple(dict.fromkeys(cues)))


@dataclass(slots=True)
class SpeechSegmenter:
    """Incremental text segmenter for low-latency speech.

    Provider deltas are buffered until a stable conversational boundary appears.
    This avoids token-by-token TTS while letting the first short clause start before
    the full model response exists.
    """

    min_chars: int = 12
    soft_max_chars: int = 92
    hard_max_chars: int = 150
    _buffer: str = field(default="", init=False)

    def feed(self, delta: str) -> list[str]:
        if not delta:
            return []
        self._buffer += delta
        ready: list[str] = []
        while True:
            boundary = self._find_boundary(self._buffer)
            if boundary is None:
                break
            segment = self._buffer[:boundary].strip()
            self._buffer = self._buffer[boundary:].lstrip()
            if segment:
                ready.append(segment)
        return ready

    def flush(self) -> list[str]:
        tail = self._buffer.strip()
        self._buffer = ""
        return [tail] if tail else []

    def _find_boundary(self, text: str) -> int | None:
        if len(text) < self.min_chars:
            return None
        for index, char in enumerate(text, start=1):
            if index < self.min_chars:
                continue
            if char in ".!?" and (index == len(text) or text[index:index + 1].isspace()):
                return index
            if char in ";:" and index >= 32:
                return index
        if len(text) >= self.soft_max_chars:
            search_to = min(len(text), self.hard_max_chars)
            cut = text.rfind(",", self.min_chars, search_to)
            if cut >= self.min_chars:
                return cut + 1
            cut = text.rfind(" ", self.min_chars, search_to)
            if cut >= self.soft_max_chars // 2:
                return cut + 1
        if len(text) >= self.hard_max_chars:
            return self.hard_max_chars
        return None
