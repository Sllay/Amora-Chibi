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
_LIST_RE = re.compile(r"(?m)^\s*(?:(\d{1,2})[.)]|[-*•])\s+(.+?)\s*$")
_EXAMPLE_RE = re.compile(r"(?i)(?<!\w)(?:exemplo|ex\.)\s*:\s*")
_VISUAL_BRACKETS_RE = re.compile(r"[\[\]{}<>]")
_SPACE_RE = re.compile(r"\s+")


@dataclass(frozen=True, slots=True)
class SpeechRender:
    display_text: str
    speech_text: str
    nonverbal_cues: tuple[str, ...] = ()


class SpeechNormalizer:
    """Convert display-oriented text into literal, understandable spoken pt-BR.

    This is deliberately more than typography stripping. The provider is asked to
    write for speech, but the Core keeps a deterministic last-mile guard so a model
    cannot make Amora sound like a chat window just by returning bullets, numbered
    items or visual example labels. Semantic content is preserved; presentation
    syntax is turned into small oral transitions.
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

        speech = self._speak_lists(speech)
        speech = _EXAMPLE_RE.sub("Por exemplo, ", speech)
        speech = _MARKDOWN_RE.sub("", speech)
        speech = _QUOTE_RE.sub("", speech)
        speech = _VISUAL_BRACKETS_RE.sub(" ", speech)
        speech = _EMOJI_RE.sub(" ", speech)
        speech = speech.replace("—", ", ").replace("–", ", ")
        # A colon is visually useful but often produces an announcer-like pause in
        # literal TTS. A comma preserves the relation without reading like a slide.
        speech = speech.replace(":", ",")
        speech = _SPACE_RE.sub(" ", speech).strip()
        speech = re.sub(r"\s+([,.!?;])", r"\1", speech)
        speech = re.sub(r"([,.!?;]){2,}", lambda m: m.group(0)[0], speech)
        return SpeechRender(
            display_text=display,
            speech_text=speech,
            nonverbal_cues=tuple(dict.fromkeys(cues)),
        )

    @staticmethod
    def _speak_lists(text: str) -> str:
        index = 0

        def replace(match: re.Match[str]) -> str:
            nonlocal index
            number = match.group(1)
            body = match.group(2).strip()
            if not body:
                return ""
            ordinal = int(number) if number and number.isdigit() else index + 1
            if ordinal <= 1:
                prefix = "Primeiro, "
            elif ordinal == 2:
                prefix = "Depois, "
            else:
                prefix = "E também, "
            index += 1
            ending = "" if body.endswith((".", "!", "?", ",", ";")) else "."
            return f"{prefix}{body}{ending}"

        return _LIST_RE.sub(replace, text)


@dataclass(slots=True)
class SpeechSegmenter:
    """Release short stable clauses so neural rendering can overlap playback.

    FINAL_B currently needs full-segment Supertonic + WORLD processing on Android.
    Keeping speech chunks conversationally small lowers time-to-first-audio and lets
    segment N+1 finish while N is playing without cutting text token-by-token.
    """

    min_chars: int = 10
    soft_max_chars: int = 64
    hard_max_chars: int = 100
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
            if char in ";:" and index >= 28:
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
