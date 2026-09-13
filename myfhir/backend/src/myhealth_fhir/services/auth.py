"""OAuth2 token management for the Anthem/Elevance Health FHIR API."""


import base64
import hashlib
import logging
import secrets
import threading
import time
from datetime import UTC, datetime
from urllib.parse import urlencode

import httpx

from myhealth_fhir.config.settings import ProviderConfig, resolve_provider
from myhealth_fhir.models.oauth import OAuthToken, TokenStore

log = logging.getLogger("myhealth_fhir.auth")


def get_auth_manager(provider: str = "anthem") -> "AuthManager":
    """Return a cached AuthManager for the given provider name."""
    config = resolve_provider(provider)
    if provider not in AuthManager.instances:
        AuthManager.instances[provider] = AuthManager(config)
    return AuthManager.instances[provider]


class AuthManager:
    """Manages the OAuth2 token lifecycle (exchange, refresh, status) for one provider."""

    REFRESH_THRESHOLD = 300
    MAX_REFRESH_RETRIES = 2
    RETRY_DELAY = 1.0

    instances: "dict[str, AuthManager]" = {}

    def __init__(self, config: ProviderConfig):
        """Initialize the AuthManager with a ProviderConfig and a DB-backed TokenStore."""
        self.config = config
        self.token_store = TokenStore(provider=config.name)
        self.lock = threading.Lock()
        self.pkce_code_verifier: str | None = None
        self._load_pkce_verifier()

    def pkce_create_pair(self) -> tuple[str, str]:
        """Generate a PKCE verifier and its S256 challenge."""
        verifier = secrets.token_urlsafe(32)[:128]
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        self.pkce_code_verifier = verifier
        return verifier, challenge

    def _save_pkce_verifier(self, verifier: str, state: str | None = None) -> None:
        """Persist the PKCE verifier to the auth DB so it survives restarts."""
        from myhealth_fhir.db import get_auth_session
        from myhealth_fhir.db.models_auth import PKCEVerifier

        with get_auth_session() as session:
            row = session.get(PKCEVerifier, self.config.name)
            if row is None:
                row = PKCEVerifier(provider=self.config.name)
                session.add(row)
            row.verifier = verifier
            if state is not None:
                row.state = state
            session.commit()

    def _load_pkce_verifier(self) -> None:
        """Load a persisted PKCE verifier from the auth DB."""
        from myhealth_fhir.db import get_auth_session
        from myhealth_fhir.db.models_auth import PKCEVerifier

        try:
            with get_auth_session() as session:
                row = session.get(PKCEVerifier, self.config.name)
                if row is not None:
                    self.pkce_code_verifier = row.verifier
        except Exception:
            self.pkce_code_verifier = None

    def _clear_pkce_verifier(self) -> None:
        """Clear the persisted PKCE verifier after successful token exchange."""
        from myhealth_fhir.db import get_auth_session
        from myhealth_fhir.db.models_auth import PKCEVerifier

        self.pkce_code_verifier = None
        try:
            with get_auth_session() as session:
                row = session.get(PKCEVerifier, self.config.name)
                if row is not None:
                    session.delete(row)
                    session.commit()
        except Exception:
            pass

    def build_authorize_url(self, state: str | None = None, scopes: list[str | None] = None) -> str:
        """Build the Anthem authorization URL with PKCE challenge."""
        if not state:
            state = secrets.token_urlsafe(16)
        scope_val = " ".join(scopes or self.config.default_scopes)
        verifier, challenge = self.pkce_create_pair()
        self.pkce_code_verifier = verifier
        self._save_pkce_verifier(verifier, state=state)
        params = {
            "client_id": self.config.client_id,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "redirect_uri": self.config.redirect_uri,
            "response_type": "code",
            "scope": scope_val,
            "state": state,
        }
        if self.config.audience_param:
            params["aud"] = self.config.fhir_base_url
        return f"{self.config.authorization_url}?{urlencode(params)}"

    def token_request(self, data: dict[str, str]) -> httpx.Response:
        """POST a token endpoint request with HTTP Basic auth."""
        cred = f"{self.config.client_id}:{self.config.client_secret}"
        encoded = base64.b64encode(cred.encode()).decode()
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Authorization": f"Basic {encoded}",
        }
        with httpx.Client(timeout=30.0) as client:
            resp = client.post(self.config.token_url, data=data, headers=headers)
        return resp

    def parse_token_response(self, body: dict, previous_token: OAuthToken | None = None) -> OAuthToken:
        """Parse a token endpoint JSON response into an OAuthToken and persist it."""
        refresh_expires = body.get("refresh_expires_in") or (
            previous_token.refresh_token_expires_in if previous_token else 2592000
        )
        id_token = body.get("id_token") or (previous_token.id_token if previous_token else None)
        refresh_token = body.get("refresh_token") or (previous_token.refresh_token if previous_token else None)
        patient_name = previous_token.patient_name if previous_token else None
        if id_token:
            try:
                import base64
                import json as _json

                payload = id_token.split(".")[1]
                decoded = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)).decode()
                claims = _json.loads(decoded)
                patient_name = (
                    claims.get("name")
                    or f"{claims.get('given_name', '')} {claims.get('family_name', '')}".strip()
                    or None
                )
            except Exception:
                pass
        token = OAuthToken(
            access_token=body["access_token"],
            token_type=body.get("token_type", "Bearer"),
            expires_in=body.get("expires_in", 3600),
            scope=body.get("scope", ""),
            refresh_token=refresh_token,
            id_token=id_token,
            obtained_at=datetime.now(UTC),
            refresh_token_expires_in=refresh_expires,
            patient_name=patient_name,  # Only set when id_token is present (initial auth)
        )
        self.token_store.save(token)
        self._clear_pkce_verifier()
        return token

    def exchange_code(self, code: str) -> OAuthToken:
        """Exchange an authorization code for access and refresh tokens."""
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self.config.redirect_uri,
        }
        if self.pkce_code_verifier:
            data["code_verifier"] = self.pkce_code_verifier
        resp = self.token_request(data)
        resp.raise_for_status()
        log.info("Authorization code exchanged successfully")
        token = self.parse_token_response(resp.json())
        pid = token.patient_id
        if pid:
            log.info("Token stored for patient %s", pid)
        if not token.refresh_token and "offline_access" not in self.config.default_scopes:
            log.warning(
                "No refresh_token returned by %s and 'offline_access' is NOT in the provider's default_scopes. "
                "Tokens will not be refreshable — you'll be forced to re-auth every ~1h. "
                "Add 'offline_access' to PROVIDERS['%s']['default_scopes'] in settings.py to fix.",
                self.config.display_name,
                self.config.name,
            )
        return token

    def refresh_token(self, old_token: OAuthToken | None = None, patient_id: str | None = None) -> OAuthToken:
        """Refresh the access token using a stored refresh token, or raise on expiry/rejection."""
        if old_token is None:
            old_token = self.token_store.load(patient_id=patient_id)

        if not old_token or not old_token.refresh_token:
            raise RuntimeError("No refresh token available")

        if old_token.refresh_expired:
            log.warning(
                "Refresh token has expired for patient %s — full re-authentication required",
                patient_id or old_token.patient_id,
            )
            cmd = self.config.auth_command()
            raise RuntimeError(f"Refresh token expired. Run '{cmd}' to re-authenticate.")

        data = {
            "grant_type": "refresh_token",
            "refresh_token": old_token.refresh_token,
        }

        for attempt in range(1 + self.MAX_REFRESH_RETRIES):
            try:
                resp = self.token_request(data)
                if resp.status_code in (401, 400):
                    log.warning(
                        "Token refresh rejected by server (attempt %d/%d) for patient %s",
                        attempt + 1,
                        1 + self.MAX_REFRESH_RETRIES,
                        patient_id or old_token.patient_id,
                    )
                    cmd = self.config.auth_command()
                    raise RuntimeError(f"Refresh token rejected by server. Run '{cmd}' to re-authenticate.")
                resp.raise_for_status()
                token = self.parse_token_response(resp.json(), previous_token=old_token)
                log.info(
                    "Token refreshed for patient %s. Access token valid for %ds",
                    token.patient_id or "default",
                    token.expires_in,
                )
                return token
            except httpx.HTTPError:
                if attempt < self.MAX_REFRESH_RETRIES:
                    time.sleep(self.RETRY_DELAY * (attempt + 1))
                else:
                    raise

    def get_valid_token(
        self,
        threshold: int | None = None,
        force_refresh: bool = False,
        patient_id: str | None = None,
    ) -> OAuthToken:
        """Return a valid access token, refreshing proactively or on demand."""
        if threshold is None:
            threshold = self.REFRESH_THRESHOLD

        with self.lock:
            token = self.token_store.load(patient_id=patient_id)

            if force_refresh and token and token.refresh_token:
                log.info("Forced token refresh requested for patient %s", patient_id or "default")
                return self.refresh_token(token, patient_id=patient_id)

            if token and not token.expired and not token.approach_expiry(threshold):
                return token

            if token and token.refresh_token:
                try:
                    return self.refresh_token(token, patient_id=patient_id)
                except RuntimeError as e:
                    log.warning("Refresh failed for patient %s: %s", patient_id or "default", e)
                    raise

            cmd = self.config.auth_command()
            raise RuntimeError(f"No valid access token. Run '{cmd}' to authenticate first.")

    def status(self, patient_id: str | None = None) -> dict:
        """Return a status summary for one or all stored patients."""
        if patient_id:
            tokens = [self.token_store.load(patient_id=patient_id)]
        else:
            tokens = self.token_store.list_tokens()

        if not tokens or all(t is None for t in tokens):
            cmd = self.config.auth_command()
            return {
                "authenticated": False,
                "message": f"No tokens stored. Run '{cmd}' to authenticate.",
            }

        token_list = []
        for t in tokens:
            if t is None:
                continue
            token_list.append(
                {
                    "patient_id": t.storage_patient_id or t.patient_id or "default",
                    "patient_name": t.patient_name,
                    "authenticated": True,
                    "expired": t.expired,
                    "refresh_expired": t.refresh_expired,
                    "seconds_remaining": round(t.seconds_remaining),
                    "expires_at": t.expires_at.isoformat(),
                    "scopes": t.scope.split() if t.scope else [],
                    "has_refresh_token": bool(t.refresh_token),
                }
            )

        return {
            "authenticated": len(token_list) > 0,
            "patient_count": len(token_list),
            "patients": token_list,
        }

    def clear(self, patient_id: str | None = None) -> None:
        """Clear stored tokens for one patient or all patients for this provider."""
        if patient_id:
            self.token_store.clear(patient_id=patient_id)
        else:
            self.token_store.clear()


class RefreshDaemon:
    """Background daemon that proactively refreshes tokens before they expire."""

    def __init__(self, provider: str = "anthem", interval: int = 1800, threshold: int = 600):
        """Initialize the daemon with provider name, poll interval, and refresh threshold."""
        self.provider = provider
        self.interval = interval
        self.threshold = threshold
        self.manager = get_auth_manager(provider)

    def run(self) -> None:
        """Run the refresh loop until interrupted by Ctrl+C."""
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(levelname)s] %(message)s",
        )
        log = logging.getLogger(f"myhealth_fhir.refresh_daemon.{self.provider}")
        patient_ids = self.manager.token_store.list_all_patient_ids()
        log.info(
            "Token refresh daemon started for '%s' (%d patient(s), interval=%ds, threshold=%ds)",
            self.provider,
            len(patient_ids) or 1,
            self.interval,
            self.threshold,
        )

        while True:
            try:
                self.attempt_refresh(log)
                time.sleep(self.interval)
            except KeyboardInterrupt:
                log.info("Daemon stopped by user")
                break
            except Exception as e:
                log.exception("Unexpected error in refresh loop: %s", e)
                time.sleep(60)

    def attempt_refresh(self, log: logging.Logger) -> None:
        """Attempt to refresh tokens for all stored patients that are near expiry."""
        patient_ids = self.manager.token_store.list_all_patient_ids()
        if not patient_ids:
            patient_ids = ["_default"]

        for pid in patient_ids:
            with self.manager.lock:
                token = self.manager.token_store.load(patient_id=pid)
                if not token:
                    log.warning("No token found for patient %s — skipping", pid)
                    continue

                if token.refresh_expired:
                    log.error(
                        "Refresh token EXPIRED for patient %s. Run '%s' to re-authenticate.",
                        pid,
                        self.manager.config.auth_command(),
                    )
                    continue

                if not token.expired and not token.approach_expiry(self.threshold):
                    remaining = int(token.seconds_remaining)
                    log.debug("Token valid for %ds for patient %s — no refresh needed", remaining, pid)
                    continue

                log.info(
                    "Refreshing token for patient %s (%ds remaining, threshold=%ds)",
                    pid,
                    int(token.seconds_remaining),
                    self.threshold,
                )
                try:
                    new = self.manager.refresh_token(token, patient_id=pid)
                    log.info(
                        "Refreshed successfully for patient %s. New token valid until %s",
                        pid,
                        new.expires_at.isoformat(),
                    )
                except RuntimeError as e:
                    log.error("Refresh failed for patient %s: %s", pid, e)
