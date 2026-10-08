from pathlib import Path
import pytest
from amora.secrets import SecretStore


def test_secret_store_keeps_values_out_of_names(tmp_path: Path) -> None:
    store = SecretStore(tmp_path / "secrets"); store.set("GEMINI_API_KEY", "super-secret-value")
    assert store.get("GEMINI_API_KEY") == "super-secret-value"
    assert store.names() == ("GEMINI_API_KEY",)
    assert "super-secret-value" not in repr(store.names())
    assert (tmp_path / "secrets" / "GEMINI_API_KEY").read_text() == "super-secret-value"


def test_secret_names_reject_path_traversal(tmp_path: Path) -> None:
    store = SecretStore(tmp_path / "secrets")
    with pytest.raises(ValueError): store.set("../BAD", "x")


def test_local_api_token_is_stable(tmp_path: Path) -> None:
    store = SecretStore(tmp_path / "secrets"); first = store.ensure_token(); second = store.ensure_token()
    assert first == second; assert len(first) >= 32
