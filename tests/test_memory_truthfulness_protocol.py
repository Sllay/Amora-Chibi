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
