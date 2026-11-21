"""Host-independent deployment intent for Amora.

The chosen profile is SOURCE-CONTROLLED, not an operator secret or a
runtime environment variable. It describes intended destinations and safe
database roles; it does not create services or silently replace the live
Aiven PG18 backend. Tokens/DSNs/CA must always be injected via a secret
manager. The release gate must verify the actual host and data lineage.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Host = Literal["northflank", "altare"]
CacheMode = Literal["off", "shadow", "read_preferred"]

# The ONLY profile switch needed after an authorized, verified host cutover.
# Keep production on Northflank until Altare passes independent E2E checks.
ACTIVE_DEPLOYMENT_PROFILE = "northflank"


@dataclass(frozen=True)
class MemoryRoles:
    authoritative: Literal["aiven_pg18"] = "aiven_pg18"
    cache: Literal["none", "altare_pg17"] = "none"
    cache_mode: CacheMode = "off"
    # Only a fresh, version-checked projection can serve noncritical reads.
    max_cache_age_seconds: int = 30
    primary_pg_major: int = 18
    cache_pg_major: int | None = None

    def __post_init__(self) -> None:
        if self.authoritative != "aiven_pg18" or self.primary_pg_major != 18:
            raise ValueError("MEMORY_AUTHORITY_MUST_REMAIN_AIVEN_PG18")
        if (self.cache == "none") != (self.cache_mode == "off"):
            raise ValueError("MEMORY_CACHE_MODE_INCOMPATIBLE")
        if self.cache == "altare_pg17" and self.cache_pg_major != 17:
            raise ValueError("MEMORY_READMODEL_PG17_REQUIRED")
        if self.cache == "none" and self.cache_pg_major is not None:
            raise ValueError("MEMORY_CACHE_VERSION_WITHOUT_CACHE")
        if not 1 <= self.max_cache_age_seconds <= 120:
            raise ValueError("MEMORY_CACHE_STALENESS_UNSAFE")


@dataclass(frozen=True)
class DeploymentProfile:
    name: str
    host: Host
    core_service: str
    discord_service: str
    web_service: str
    memory: MemoryRoles

    def __post_init__(self) -> None:
        if self.host not in {"northflank", "altare"}:
            raise ValueError("UNKNOWN_DEPLOYMENT_HOST")
        if not all((self.core_service, self.discord_service, self.web_service)):
            raise ValueError("DEPLOYMENT_SERVICES_REQUIRED")
        if self.memory.cache == "altare_pg17" and self.host != "altare":
            raise ValueError("READMODEL_MUST_BE_HOSTED_WITH_ALTARE_CORE")


PROFILES = {
    "northflank": DeploymentProfile(
        name="northflank", host="northflank",
        core_service="amora-core", discord_service="amora-discord",
        web_service="amora-web",
        memory=MemoryRoles(),
    ),
    "altare": DeploymentProfile(
        name="altare", host="altare",
        core_service="amora-core", discord_service="amora-discord",
        web_service="amora-web",
        # Initially shadow-only. NEVER use PG17 reads in production until a
        # signed-off cache correctness/latency/authorization test is complete.
        memory=MemoryRoles(
            cache="altare_pg17", cache_mode="shadow", cache_pg_major=17,
        ),
    ),
}


def get_profile(name: str | None = None) -> DeploymentProfile:
    selected = ACTIVE_DEPLOYMENT_PROFILE if name is None else name
    if selected not in PROFILES:
        raise ValueError("UNKNOWN_DEPLOYMENT_PROFILE")
    return PROFILES[selected]


def release_plan(name: str | None = None) -> dict[str, object]:
    """Nonsecret deterministic plan for CI, audit and release documentation."""
    profile = get_profile(name)
    return {
        "profile": profile.name,
        "host": profile.host,
        "services": {
            "core": profile.core_service,
            "discord": profile.discord_service,
            "web": profile.web_service,
        },
        "data": {
            "authoritative": profile.memory.authoritative,
            "postgres_major": profile.memory.primary_pg_major,
            "read_model": profile.memory.cache,
            "read_model_pg_major": profile.memory.cache_pg_major,
            "read_model_mode": profile.memory.cache_mode,
            "max_staleness_s": profile.memory.max_cache_age_seconds,
        },
        "release_gates": [
            "verified_backup_restore",
            "secrets_injected_outside_git",
            "single_active_discord_gateway",
            "cache_freshness_and_owner_scope",
            "host_72h_observation",
            "explicit_cutover_approval",
        ],
        "note": "Plan only: does not deploy, copy secrets or reconfigure the live database.",
    }
