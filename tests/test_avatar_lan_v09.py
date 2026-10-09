from amora.avatar.cli import _lan_path_allowed


def test_avatar_lan_allows_only_required_routes() -> None:
    allowed = [
        "/",
        "/health",
        "/avatar/",
        "/avatar/app.js",
        "/avatar/engine.js",
        "/v1/avatar/project",
        "/v1/avatar/assets/hair_front",
        "/v1/avatar/state",
        "/v1/pairing/redeem",
    ]
    blocked = [
        "/v1/conversation/state",
        "/v1/audio/transcribe",
        "/v1/autonomy/state",
        "/v1/actions/plans",
        "/docs",
        "/openapi.json",
    ]

    assert all(_lan_path_allowed(path) for path in allowed)
    assert all(not _lan_path_allowed(path) for path in blocked)
