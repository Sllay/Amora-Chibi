import pytest

from amora.cognition.context import resolve_scene_visibility
from amora.cognition.models import CognitiveRequest
from amora.events import PrivacyScope


@pytest.mark.parametrize("channel,expected", [
    ("dm", "private"),
    ("group", "group"),
    ("unknown", "group"),
    (None, "group"),
])
def test_messaging_visibility(channel, expected):
    request = CognitiveRequest(
        text="hello", speaker_id="PERSON_TEST",
        metadata={"surface": "platform_messaging", "channel_type": channel},
    )
    assert resolve_scene_visibility(request) == expected


def test_public_request_is_shared():
    request = CognitiveRequest(
        text="hello", speaker_id="PERSON_TEST",
        privacy_scope=PrivacyScope.PUBLIC,
    )
    assert resolve_scene_visibility(request) == "group"
