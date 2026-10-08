from __future__ import annotations

from .models import SpeakerResult


class SessionSpeakerTracker:
    """Maps diarization labels to stable session-local speaker IDs.

    A future Android/sherpa-onnx adapter can supply diarization labels and
    optional known-person matches. Voice similarity remains contextual evidence
    and never becomes authentication.
    """

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self._labels: dict[str, str] = {}
        self._counter = 0

    def resolve(
        self,
        *,
        diarization_label: str | None = None,
        known_person_id: str | None = None,
        confidence: float = 0.5,
    ) -> SpeakerResult:
        if known_person_id:
            return SpeakerResult(
                speaker_id=known_person_id,
                known_person_id=known_person_id,
                diarization_label=diarization_label,
                confidence=confidence,
                reason_codes=["KNOWN_PERSON_MATCH_CONTEXT_ONLY"],
            )

        if diarization_label:
            if diarization_label not in self._labels:
                self._counter += 1
                self._labels[diarization_label] = f"{self.session_id}_SPEAKER_{self._counter}"
            return SpeakerResult(
                speaker_id=self._labels[diarization_label],
                diarization_label=diarization_label,
                confidence=confidence,
                reason_codes=["SESSION_DIARIZATION_LABEL"],
            )

        return SpeakerResult(
            speaker_id=f"{self.session_id}_SPEAKER_UNKNOWN",
            confidence=0.2,
            reason_codes=["SPEAKER_UNKNOWN"],
        )
