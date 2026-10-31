from pathlib import Path
from amora.app import AmoraCore


def test_bootstrap_survives_restart_with_same_identity(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora")); first = AmoraCore.bootstrap()
    try:
        assert first.identities.resolve_alias("Bad King").id == "PERSON_BK"; token = first.secrets.get("LOCAL_API_TOKEN"); assert token
        assert first.identities.resolve_alias("Lucas").id == "PERSON_LUCAS"
        assert first.identities.resolve_alias("Nicolas").id == "PERSON_NICOLAS"
        assert first.identities.relationship("PERSON_BK", "father_of", "PERSON_LUCAS") is not None
        assert first.identities.relationship("PERSON_LUCAS", "sibling_of", "ENTITY_AMORA") is not None
        assert first.identities.relationship("PERSON_NICOLAS", "cousin_of", "ENTITY_AMORA") is not None
    finally: first.close()
    second = AmoraCore.bootstrap()
    try:
        assert second.identities.resolve_alias("Slay").id == "PERSON_BK"
        assert second.identities.resolve_alias("Lucas").id == "PERSON_LUCAS"
        assert second.identities.resolve_alias("Nicolas").id == "PERSON_NICOLAS"
        assert second.secrets.get("LOCAL_API_TOKEN") == token
        report = second.health_report()
        assert report["schema_version"] == 13
        assert report["person_profile_count"] == 3
        assert report["platform_identity_binding_count"] == 0
        assert report["family_authority_ids"] == ["PERSON_BK"]
        assert report["memory_count"] == 0
        assert report["world_claim_count"] == 0
        assert report["self_core_identity"] == "ENTITY_AMORA"
        assert report["self_entry_count"] >= 3
        assert report["emotion_event_count"] == 0
        assert report["pending_social_bids"] == 0
        assert report["autonomy_decision_count"] == 0
        assert report["active_intention_kind"] is None
        assert len(report["drives"]) == 7
        assert report["action_plan_count"] == 0
        assert report["action_run_count"] == 0
        assert report["tool_execution_count"] == 0
        assert report["plugin_count"] == 1
        assert report["skill_count"] == 2
        assert report["turn_decision_count"] == 0
        assert report["speech_pending_count"] == 0
        assert report["speech_spoken_count"] == 0
        assert report["dialogue_provider"] == "none"
        assert report["cognition_enabled"] is True
        assert report["cognitive_message_count"] == 0
        assert report["cognitive_journal_count"] == 0
        assert report["messaging_audit_count"] == 0
        assert report["messaging_outbound_pending_count"] == 0
        assert report["platform_presence"] == []
        assert report["avatar_project_count"] == 0
        assert report["avatar_asset_count"] == 0
        assert report["avatar_expression"] == "neutral"
        assert report["avatar_speaking"] is False
    finally: second.close()
