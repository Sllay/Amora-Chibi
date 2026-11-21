"""No-network tests for public Altare domain output filtering."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.ops.altare_public_domain_probe import https_domains, HOSTNAME


def test_exact_https_active_only():
    p = {"items": [
        {"hostname": "core-amora.go.altare.global", "kind": "https", "status": "active"},
        {"hostname": "evil.go.altare.global.attacker.com", "kind": "https", "status": "active"},
        {"hostname": "old.go.altare.global", "kind": "a", "status": "active"},
        {"hostname": "pending.go.altare.global", "kind": "https", "status": "pending"},
    ]}
    assert https_domains(p) == ["https://core-amora.go.altare.global"]


def test_https_exact_dns_contract():
    assert HOSTNAME.fullmatch("core-amora.go.altare.global")
    assert not HOSTNAME.fullmatch("fi-hel03.altare.global")
    assert not HOSTNAME.fullmatch("core-amora.go.altare.global:25193")
