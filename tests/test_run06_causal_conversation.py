"""RUN06: causal separation without destructive grouping or invented ACK."""
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from amora.cognition.causal_conversation import (
    causal_partition, composition_window, followup_kind,
)
from amora.cognition.models import CognitiveRequest, ConversationMessage, MessageSource


def _input(request, text, at, **overrides):
    turn = uuid4()
    values = dict(turn_id=turn, speaker_id=request.speaker_id, addressee_id=request.target_id,
                  source=MessageSource("text"), raw_text=text, started_at=at,
                  session_id=request.session_id, remote_processing_allowed=True,
                  dedupe_key=f"cognitive-input:{turn}",
                  metadata={"reply_enqueue_requested":True})
    values.update(overrides)
    return ConversationMessage(**values)


def test_causal_hints_and_bounded_wait():
    assert composition_window("Oi, tudo bem?") == 0
    assert 0 < composition_window("Quero falar sobre o Godot...") < 1
    assert followup_kind("Quero Godot", "Na verdade, Godot para Android") == "correction"
    assert followup_kind("Quero Godot", "E também Godot para Linux") == "continuation"
    assert followup_kind("Quero Godot", "Na verdade, Blender") == "uncertain_correction"
    assert followup_kind("Quero Godot", "Mudando de assunto, Godot") == "topic_change"
    assert followup_kind("Quero Godot", "Espera, Godot") == "interruption"
    assert followup_kind("Quero Godot", "Qual sua cor favorita?") == "new_question"


def test_partition_stops_at_new_topic_and_preserves_literal_and_ids():
    now = datetime(2026,11,24,tzinfo=timezone.utc)
    req = CognitiveRequest(text="Quero Godot para PC", speaker_id="PERSON_BK",
                           session_id="dm", remote_processing_allowed=True)
    past = _input(req, "Oi", now-timedelta(seconds=2))
    anchor = _input(req, req.text, now, turn_id=req.turn_id,
                    dedupe_key=f"cognitive-input:{req.turn_id}")
    correction = _input(req, "Na verdade, Godot para Android.", now+timedelta(seconds=1))
    independent = _input(req, "Outra pergunta, qual é a capital?", now+timedelta(seconds=2))
    after = _input(req, "E também Godot para Linux.", now+timedelta(seconds=3))
    rows = [past, anchor, correction, independent, after]
    history, related, state = causal_partition(req, rows, allowed=lambda m: True)
    assert state == "partitioned"
    assert [m.turn_id for m in history] == [past.turn_id, anchor.turn_id]
    assert len(related) == 1
    assert related[0]["turn_id"] == str(correction.turn_id)
    assert related[0]["literal_text"] == correction.raw_text
    assert related[0]["independent_reply_still_pending"]
    assert req.text == anchor.raw_text and len(rows) == 5


def test_partition_fails_closed_on_ties_privacy_and_missing_anchor():
    now = datetime(2026,11,24,tzinfo=timezone.utc)
    req = CognitiveRequest(text="Godot para PC",speaker_id="PERSON_BK",session_id="dm")
    anchor = _input(req,req.text,now,turn_id=req.turn_id,
                    dedupe_key=f"cognitive-input:{req.turn_id}")
    tied = _input(req,"Na verdade Godot Android",now)
    private = _input(req,"Na verdade Godot Android",now+timedelta(seconds=1),remote_processing_allowed=False)
    history, related, _ = causal_partition(req,[anchor,tied,private],allowed=lambda m:True)
    assert [m.turn_id for m in history] == [anchor.turn_id]
    assert not related
    assert causal_partition(req,[private],allowed=lambda m:True) == ([],[],"anchor_missing")
