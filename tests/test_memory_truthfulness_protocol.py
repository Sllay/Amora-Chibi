"""Memory claims must be grounded in Core-provided evidence."""
from amora.cognition.prompt_frame import provider_constitution


def test_provider_memory_claims_require_retrieved_evidence():
    constitution = provider_constitution()
    assert "claim_to_remember_unrecorded_events" in constitution["authority"]["provider_may_not"]
    policy = constitution["memory_truthfulness"]
    assert "memories" in policy["rule"]
    assert "world_claims" in policy["rule"]
    assert "recent conversation" in policy["rule"]
    assert "Não lembro direito" in policy["when_evidence_missing"]
    assert "not proof" in policy["evidence_scope"]


def test_provider_cannot_guess_current_discord_speaker_or_person_preferences():
    policy = provider_constitution()["person_identity_and_preferences"]
    assert "canonical speaker identity" in policy["identity_authority"]
    assert "not necessarily the current speaker" in policy["identity_authority"]
    assert "cannot verify who is speaking" in policy["unknown_speaker"]
    assert "text-only Discord" in policy["unknown_speaker"]
    assert "likes, dislikes" in policy["third_party_preferences"]
    assert "fabricated biography" in policy["third_party_preferences"]
