from __future__ import annotations

from test_cloud_web_incident import safari


def test_web_preview_uses_existing_authenticated_state_scope(safari) -> None:
    _, _, _, client, headers = safari
    preview_id = "preview12345678"
    response = client.get(f"/v1/web/state?preview_id={preview_id}", headers=headers)
    assert response.status_code == 200, response.text
    assert response.json() == {"text": "", "done": False, "error": None}
    assert response.headers["cache-control"] == "no-store"


def test_invalid_preview_id_is_rejected_without_storage_lookup(safari) -> None:
    _, _, _, client, headers = safari
    response = client.get("/v1/web/state?preview_id=x", headers=headers)
    assert response.status_code == 200
    assert response.json()["done"] is True
    assert response.json()["error"] == "INVALID_PREVIEW_ID"


def test_web_turn_exposes_request_scoped_context_and_remote_sql_latency(safari) -> None:
    _, _, _, client, headers = safari
    response = client.post(
        "/v1/web/utterance",
        headers={**headers, "X-Amora-Preview-Id": "latencytrace1234"},
        json={
            "text": "Oi Amora",
            "remote_processing_allowed": False,
            "test_mode": True,
        },
    )
    assert response.status_code == 200, response.text
    assert response.headers["X-Amora-Latency-Trace-Version"] == "v3"
    context_ms = float(response.headers["X-Amora-Latency-Context-Ms"])
    remote_sql_ms = float(response.headers["X-Amora-Latency-Remote-Sql-Ms"])
    remote_sql_calls = int(response.headers["X-Amora-Latency-Remote-Sql-Calls"])
    remote_sql_max_ms = float(response.headers["X-Amora-Latency-Remote-Sql-Max-Ms"])
    read_ms = float(response.headers["X-Amora-Latency-Remote-Sql-Read-Ms"])
    read_calls = int(response.headers["X-Amora-Latency-Remote-Sql-Read-Calls"])
    write_ms = float(response.headers["X-Amora-Latency-Remote-Sql-Write-Ms"])
    write_calls = int(response.headers["X-Amora-Latency-Remote-Sql-Write-Calls"])
    txn_ms = float(response.headers["X-Amora-Latency-Remote-Sql-Txn-Ms"])
    txn_calls = int(response.headers["X-Amora-Latency-Remote-Sql-Txn-Calls"])
    other_calls = int(response.headers.get("X-Amora-Latency-Remote-Sql-Other-Calls", "0"))
    assert context_ms >= 0.0
    assert remote_sql_ms >= 0.0
    assert remote_sql_calls >= 1
    assert 0.0 <= remote_sql_max_ms <= remote_sql_ms
    assert read_ms >= 0.0 and read_calls >= 1
    assert write_ms >= 0.0 and write_calls >= 1
    assert txn_ms >= 0.0 and txn_calls >= 1
    assert read_calls + write_calls + txn_calls + other_calls == remote_sql_calls


def test_safari_lab_assets_disable_runtime_module_caching(safari) -> None:
    _, _, _, client, _ = safari
    response = client.get("/web/")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store, max-age=0"
