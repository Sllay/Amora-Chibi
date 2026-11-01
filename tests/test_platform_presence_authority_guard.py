from __future__ import annotations

import pytest

from amora.app import AmoraCore
from amora.identity import PersonProfileSeed
from amora.time import SimulatedClock


def test_generic_profile_write_preserves_omitted_presence_authority_and_rejects_unconfirmed_change(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    core = AmoraCore.bootstrap(clock=SimulatedClock())
    try:
        current = core.profiles.profile("PERSON_BK")
        assert current is not None and current.can_control_platform_presence

        legacy_payload = current.model_dump(mode="json")
        legacy_payload.pop("can_control_platform_presence")
        legacy_payload["short_description"] = "perfil salvo por cliente antigo"
        legacy = PersonProfileSeed.model_validate(legacy_payload)
        assert "can_control_platform_presence" not in legacy.model_fields_set
        saved = core.profiles.upsert_profile(legacy)
        assert saved.can_control_platform_presence is True
        assert core.profiles.profile("PERSON_BK").can_control_platform_presence is True

        attempted = saved.model_copy(update={"can_control_platform_presence": False})
        with pytest.raises(ValueError, match="dedicated confirmed mutation"):
            core.profiles.upsert_profile(attempted)
        assert core.profiles.profile("PERSON_BK").can_control_platform_presence is True
    finally:
        core.close()
