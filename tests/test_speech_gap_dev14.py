from amora.cognition.speech import SpeechSegmenter


def test_tiny_story_lead_is_not_emitted_as_isolated_tts_chunk() -> None:
    segmenter = SpeechSegmenter()
    text = "Era uma vez. Uma menina curiosa encontrou uma luz estranha no quintal."
    chunks = segmenter.feed(text) + segmenter.flush()

    assert chunks
    assert chunks[0] != "Era uma vez."
    assert len(chunks[0]) >= 24
    assert all(len(chunk) <= 110 for chunk in chunks)
