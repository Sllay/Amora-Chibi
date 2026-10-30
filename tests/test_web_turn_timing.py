from __future__ import annotations

from test_cloud_web_incident import safari


def test_web_text_turn_reports_server_phase_timing(safari) -> None:
    _, _, _, client, headers = safari
    response = client.post(
        "/v1/web/utterance",
        json={
            "text": "oi",
            "remote_processing_allowed": True,
            "test_mode": True,
        },
        headers=headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    timing = body["timing"]

    assert timing["total_ms"] >= 0
    assert timing["language_social_ms"] >= 0
    assert timing["conversation_ms"] >= 0
    assert timing["total_ms"] >= timing["language_social_ms"]
    assert timing["total_ms"] >= timing["conversation_ms"]
    assert timing["cognition_ms"] is not None
    assert timing["cognition_ms"] >= 0
    assert timing["local_overhead_ms"] is not None
    assert timing["local_overhead_ms"] >= 0
