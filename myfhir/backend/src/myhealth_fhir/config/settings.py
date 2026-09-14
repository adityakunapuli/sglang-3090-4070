"""Provider configuration: env loading and per-provider FHIR/OAuth profile lookup.

Providers are declared in ``PROVIDERS``. Adding a new Epic system is
config-only:

1. Add a profile here (kind ``"epic"``, URLs, env-var prefix).
2. Add a ``myhealth_<name>`` entry to ``PROVIDER_DB`` in ``db/__init__.py``.
3. Export ``<PREFIX>_CLIENT_ID`` / ``<PREFIX>_SECRET`` (fallbacks keep UCLA's
   existing ``EPIC_CLIENT_ID`` / ``EPIC_SECRET`` working).
"""

import os
from dataclasses import dataclass

from dotenv import load_dotenv, find_dotenv

# Best-effort env loading: the app works purely from exported env vars
# (Docker compose injects them), so a missing .env must not crash import.
load_dotenv(find_dotenv(), override=True)


@dataclass
class ProviderConfig:
    """Configuration for a single FHIR provider."""

    name: str
    display_name: str
    client_id: str
    client_secret: str
    authorization_url: str
    token_url: str
    redirect_uri: str
    fhir_base_url: str
    default_scopes: list[str]
    audience_param: bool = True
    kind: str = "epic"  # "anthem" | "epic"

    def auth_command(self) -> str:
        """Return the CLI command string a user should run to authenticate."""
        return f"myhealth {self.name} auth login"


# ── Provider Profiles ──────────────────────────────────────────────

PROVIDERS: dict[str, dict] = {
    "anthem": {
        "kind": "anthem",
        "display_name": "Anthem (Elevance Health)",
        "authorization_url": "https://totalview.healthos.elevancehealth.com/oauth2.code/registered/api/v1/authorize",
        "token_url": "https://totalview.healthos.elevancehealth.com/client.oauth2/registered/api/v1/token",
        "redirect_uri": "https://localhost/callback",
        "default_scopes": [
            "openid",
            "profile",
            "fhirUser",
            "launch/patient",
            "offline_access",
            "patient/*.read",
        ],
        "audience_param": True,
    },
    "ucla": {
        "kind": "epic",
        "display_name": "UCLA Health (Epic)",
        "authorization_url": "https://arrprox.mednet.ucla.edu/FHIRPRD/oauth2/authorize",
        "token_url": "https://arrprox.mednet.ucla.edu/FHIRPRD/oauth2/token",
        "redirect_uri": "https://localhost/callback",
        "fhir_base_url": "https://arrprox.mednet.ucla.edu/FHIRPRD/api/FHIR/R4",
        "default_scopes": [
            "openid",
            "fhirUser",
            "offline_access",
            "patient/*.read",
        ],
        "audience_param": True,
    },
}


def build_fhir_base_url(plan: str) -> str:
    """Build the Anthem FHIR base URL for a given plan name."""
    return f"https://totalview.healthos.elevancehealth.com/resources/registered/{plan}/api/v1/fhir"


def _resolve_epic_credentials(name: str) -> tuple[str, str]:
    """Return (client_id, client_secret) for an Epic provider using its env prefix.

    UCLA-specific fallbacks are retained for backward compatibility with the
    historic ``EPIC_CLIENT_ID`` / ``EPIC_SECRET`` / ``UCLA_SECRET`` variables.
    """
    prefix = name.upper()
    client_id = os.environ.get(f"{prefix}_CLIENT_ID") or os.environ.get("EPIC_CLIENT_ID", "")
    secret = (
        os.environ.get(f"{prefix}_SECRET")
        or os.environ.get("UCLA_SECRET")
        or os.environ.get("UCLA_SECRET_PROD")
        or os.environ.get("EPIC_SECRET", "")
    )
    return client_id, secret


def resolve_provider(name: str = "anthem") -> ProviderConfig:
    """Build provider config from profile + env vars."""
    profile = PROVIDERS.get(name)
    if not profile:
        valid = ", ".join(PROVIDERS.keys())
        raise ValueError(f"Unknown provider '{name}'. Available: {valid}")

    redirect_uri = os.environ.get("REDIRECT_URI", profile["redirect_uri"])

    if profile.get("kind") == "anthem":
        plan = os.environ.get("FHIR_PLAN", "AnthemBlueCross")
        return ProviderConfig(
            name=name,
            display_name=profile["display_name"],
            client_id=os.environ.get("CLIENT_ID", ""),
            client_secret=os.environ.get("CLIENT_SECRET", ""),
            authorization_url=profile["authorization_url"],
            token_url=profile["token_url"],
            redirect_uri=redirect_uri,
            fhir_base_url=build_fhir_base_url(plan),
            default_scopes=profile["default_scopes"],
            audience_param=profile["audience_param"],
            kind="anthem",
        )

    client_id, client_secret = _resolve_epic_credentials(name)
    return ProviderConfig(
        name=name,
        display_name=profile["display_name"],
        client_id=client_id,
        client_secret=client_secret,
        authorization_url=profile["authorization_url"],
        token_url=profile["token_url"],
        redirect_uri=redirect_uri,
        fhir_base_url=profile["fhir_base_url"],
        default_scopes=profile["default_scopes"],
        audience_param=profile["audience_param"],
        kind="epic",
    )


def list_providers() -> list[str]:
    """Return the configured provider names in stable order."""
    return list(PROVIDERS.keys())


def list_epic_providers() -> list[str]:
    """Return the configured Epic (clinical) provider names."""
    return [name for name, profile in PROVIDERS.items() if profile.get("kind") == "epic"]