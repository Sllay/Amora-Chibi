"""Canonical messaging scope identifier shared by ingress and delivery."""
import hashlib


def messaging_session_ref(platform: str, account_scope: str, conversation_id: str) -> str:
    digest = hashlib.sha256((account_scope + "|" + conversation_id).encode("utf-8")).hexdigest()[:20]
    return f"MSG_{platform.upper()}_{digest}"
