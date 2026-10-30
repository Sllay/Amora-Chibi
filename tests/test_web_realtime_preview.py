from __future__ import annotations

from test_cloud_web_incident import safari


def test_web_preview_endpoint_is_authenticated_and_ephemeral(safari) -> None:
    _, _, _, client, headers = safari
    preview_id = "preview12345678"
    response = client.get(f"/v1/web/previews/{preview_id}", headers=headers)
    assert response.status_code == 200, response.text
    assert response.json() == {"text": "", "done": False, "error": None}


def test_invalid_preview_id_is_rejected_without_storage_lookup(safari) -> None:
    _, _, _, client, headers = safari
    response = client.get("/v1/web/previews/x", headers=headers)
    assert response.status_code == 200
    assert response.json()["done"] is True
    assert response.json()["error"] == "INVALID_PREVIEW_ID"
