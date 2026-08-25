"""Google OAuth2 manager for per-user GWS authentication.

Handles OAuth flow, token storage/refresh, and credential management.
Supports two token sources:
1. Local token files in data/gws_tokens/{email}.json (legacy separate auth)
2. Open WebUI's SQLite DB (single Google login for everything)
"""

import base64
import hashlib
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Optional

import structlog
from cryptography.fernet import Fernet
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow

from app.config import get_settings

logger = structlog.get_logger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/calendar.readonly",
]

CredentialLoadStatus = Literal["ready", "disconnected", "invalid", "transient_error"]


@dataclass(frozen=True)
class CredentialLoadOutcome:
    """Explicit credential state used by destructive personal-data callers."""

    status: CredentialLoadStatus
    credentials: Optional[Credentials] = None
    error_code: str = ""

    @property
    def definitive_disconnect(self) -> bool:
        return self.status in {"disconnected", "invalid"}


def _is_definitive_oauth_error(exc: BaseException) -> bool:
    """Recognize provider responses that mean retrying the stored grant is unsafe."""

    message = str(exc).casefold()
    return "invalid_grant" in message or "revoked" in message


class GoogleAuthManager:
    """Manages per-user Google OAuth2 credentials."""

    def __init__(self):
        self.settings = get_settings()
        self.token_dir = Path(self.settings.gws_token_dir)
        self.token_dir.mkdir(parents=True, exist_ok=True)
        self._fernet = None

    def _get_fernet(self) -> Optional[Fernet]:
        """Get Fernet instance for decrypting Open WebUI tokens."""
        if self._fernet is not None:
            return self._fernet
        key = self.settings.openwebui_secret_key
        if not key:
            return None
        if len(key) != 44:
            key_bytes = hashlib.sha256(key.encode()).digest()
            fernet_key = base64.urlsafe_b64encode(key_bytes)
        else:
            fernet_key = key.encode()
        self._fernet = Fernet(fernet_key)
        return self._fernet

    def _token_path(self, user_email: str) -> Path:
        """Get token file path for a user."""
        safe_name = user_email.replace("@", "_at_").replace(".", "_")
        return self.token_dir / f"{safe_name}.json"

    def _client_config(self) -> dict:
        """Build OAuth client config from settings."""
        return {
            "web": {
                "client_id": self.settings.google_oauth_client_id,
                "client_secret": self.settings.google_oauth_client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": [self.settings.google_oauth_redirect_uri],
            }
        }

    def has_credentials(self, user_email: str) -> bool:
        """Quick check if user has stored credentials (no refresh, no API call)."""
        token_path = self._token_path(user_email)
        if token_path.exists():
            return True
        return False

    def get_stored_google_email(self, user_email: str) -> str:
        """Read google_email from token file (no API call)."""
        token_path = self._token_path(user_email)
        if not token_path.exists():
            return ""
        try:
            data = json.loads(token_path.read_text(encoding="utf-8"))
            return data.get("google_email", "")
        except Exception:
            return ""

    def get_credential_identity(self, user_email: str) -> str:
        """Return a non-reversible identity for the currently stored credential.

        Access tokens rotate during a normal refresh, so the identity uses only
        stable credential/account fields.  It is held in memory briefly and is
        never logged or persisted with briefing content.
        """
        token_path = self._token_path(user_email)
        if not token_path.exists():
            return ""
        try:
            data = json.loads(token_path.read_text(encoding="utf-8"))
            stable = {
                "refresh_token": data.get("refresh_token", ""),
                "client_id": data.get("client_id", ""),
                "scopes": sorted(data.get("scopes") or []),
                "google_email": str(data.get("google_email", "")).strip().lower(),
            }
            if not stable["refresh_token"]:
                stable["token"] = data.get("token", "")
            encoded = json.dumps(stable, sort_keys=True, separators=(",", ":"))
            return hashlib.sha256(encoded.encode()).hexdigest()
        except (OSError, TypeError, ValueError):
            return ""

    def get_credentials(self, user_email: str) -> Optional[Credentials]:
        """Backward-compatible credentials-only wrapper.

        Args:
            user_email: User's email address.

        Returns:
            Valid Credentials, or None if not authenticated.
        """
        credentials = self._get_credentials_from_file(user_email)
        if credentials is not None:
            return credentials
        return self._get_credentials_from_openwebui(user_email)

    def load_credentials(self, user_email: str) -> CredentialLoadOutcome:
        """Load credentials without collapsing definitive and retryable failures."""

        file_outcome = self._load_credentials_from_file(user_email)
        if file_outcome.status != "disconnected":
            return file_outcome
        return self._load_credentials_from_openwebui(user_email)

    def _get_credentials_from_file(self, user_email: str) -> Optional[Credentials]:
        """Backward-compatible local-file credentials-only wrapper."""

        return self._load_credentials_from_file(user_email).credentials

    def _load_credentials_from_file(self, user_email: str) -> CredentialLoadOutcome:
        """Load a local credential while retaining failure semantics."""

        token_path = self._token_path(user_email)
        if not token_path.exists():
            return CredentialLoadOutcome("disconnected", error_code="oauth_missing")

        try:
            creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
            # Token file doesn't store expiry, so always proactively refresh
            # to ensure the access token is fresh (they expire after ~1 hour)
            if creds.refresh_token:
                creds.refresh(Request())
                # Preserve google_email from existing file
                google_email = self.get_stored_google_email(user_email)
                self._save_credentials(
                    user_email, creds, google_email=google_email
                )
                logger.info("token_refreshed", source="file")
                return CredentialLoadOutcome("ready", credentials=creds)
            if creds.valid:
                return CredentialLoadOutcome("ready", credentials=creds)
            return CredentialLoadOutcome("invalid", error_code="oauth_expired")
        except Exception as e:
            logger.error("token_load_failed", source="file", error_type=type(e).__name__)
            if _is_definitive_oauth_error(e):
                return CredentialLoadOutcome("invalid", error_code="oauth_expired")
            return CredentialLoadOutcome("transient_error", error_code="google_error")

    def _get_credentials_from_openwebui(self, user_email: str) -> Optional[Credentials]:
        """Backward-compatible Open WebUI credentials-only wrapper."""

        return self._load_credentials_from_openwebui(user_email).credentials

    def _load_credentials_from_openwebui(self, user_email: str) -> CredentialLoadOutcome:
        """Load credentials from Open WebUI without erasing retryable failures.

        Open WebUI stores OAuth tokens encrypted with Fernet in its oauth_session table.
        We extract the token via docker exec to avoid SQLite locking issues.
        """
        secret_key = self.settings.openwebui_secret_key
        if not secret_key:
            return CredentialLoadOutcome("disconnected", error_code="oauth_missing")

        try:
            import subprocess
            # Run a Python script inside the Docker container to extract and decrypt the token
            script = f'''
import sqlite3, json, base64, hashlib
from cryptography.fernet import Fernet
key = "{secret_key}"
key_bytes = hashlib.sha256(key.encode()).digest()
fernet = Fernet(base64.urlsafe_b64encode(key_bytes))
conn = sqlite3.connect("/app/backend/data/webui.db", timeout=5)
cur = conn.execute("SELECT id FROM user WHERE email = ?", ("{user_email}",))
row = cur.fetchone()
if not row:
    print("NO_USER")
    conn.close()
    exit()
uid = row[0]
cur = conn.execute("SELECT token FROM oauth_session WHERE user_id = ? ORDER BY created_at DESC LIMIT 1", (uid,))
row = cur.fetchone()
conn.close()
if not row:
    print("NO_SESSION")
    exit()
try:
    d = fernet.decrypt(row[0].encode()).decode()
    print(d)
except:
    print("DECRYPT_FAIL")
'''
            result = subprocess.run(
                ["docker", "exec", "skin1004-open-webui", "python3", "-c", script],
                capture_output=True, text=True, timeout=300,
            )
            output = result.stdout.strip()
            if output in ("NO_USER", "NO_SESSION"):
                logger.warning("openwebui_token_extract", result_code=output)
                return CredentialLoadOutcome("disconnected", error_code="oauth_missing")
            if result.returncode != 0 or not output or output == "DECRYPT_FAIL":
                if output:
                    logger.warning("openwebui_token_extract", result_code="DECRYPT_FAIL")
                return CredentialLoadOutcome("transient_error", error_code="google_error")

            token_data = json.loads(output)

            access_token = token_data.get("access_token")
            refresh_token = token_data.get("refresh_token")

            if not access_token:
                logger.warning("openwebui_token_no_access")
                return CredentialLoadOutcome("invalid", error_code="oauth_expired")

            # Check if token has GWS scopes
            scope_str = token_data.get("scope", "")
            has_gws_scopes = any(
                s in scope_str
                for s in ["gmail.readonly", "calendar.readonly", "drive.readonly"]
            )
            if not has_gws_scopes:
                logger.warning("openwebui_token_missing_gws_scopes")
                return CredentialLoadOutcome("disconnected", error_code="oauth_missing")

            # Build credentials
            creds = Credentials(
                token=access_token,
                refresh_token=refresh_token,
                token_uri="https://oauth2.googleapis.com/token",
                client_id=self.settings.google_oauth_client_id,
                client_secret=self.settings.google_oauth_client_secret,
                scopes=SCOPES,
            )

            # If expired and has refresh_token, try refreshing
            if creds.expired and refresh_token:
                try:
                    creds.refresh(Request())
                    logger.info("openwebui_token_refreshed")
                except Exception as e:
                    logger.warning("openwebui_token_refresh_failed", error_type=type(e).__name__)
                    if _is_definitive_oauth_error(e):
                        return CredentialLoadOutcome("invalid", error_code="oauth_expired")
                    return CredentialLoadOutcome("transient_error", error_code="google_error")

            if creds.valid:
                logger.info("openwebui_token_loaded")
                return CredentialLoadOutcome("ready", credentials=creds)

            # Token might still be valid even if expired flag is uncertain
            # Try returning it and let the API call determine
            if access_token:
                logger.info("openwebui_token_loaded_unchecked")
                return CredentialLoadOutcome("ready", credentials=creds)

            return CredentialLoadOutcome("invalid", error_code="oauth_expired")

        except Exception as e:
            logger.error("openwebui_token_error", error_type=type(e).__name__)
            if _is_definitive_oauth_error(e):
                return CredentialLoadOutcome("invalid", error_code="oauth_expired")
            return CredentialLoadOutcome("transient_error", error_code="google_error")

    def get_auth_url(self, user_email: str, *, state: str, redirect_uri: str = "") -> str:
        """Generate Google OAuth2 authorization URL.

        Args:
            user_email: User's email used as Google's login hint.
            state: Signed, single-use state issued by the authenticated application user.
            redirect_uri: Dynamic redirect URI from request host. Falls back to config.

        Returns:
            Authorization URL to redirect user to.
        """
        uri = redirect_uri or self.settings.google_oauth_redirect_uri
        flow = Flow.from_client_config(
            self._client_config(),
            scopes=SCOPES,
            redirect_uri=uri,
        )
        auth_url, _ = flow.authorization_url(
            access_type="offline",
            prompt="consent",
            state=state,
            login_hint=user_email,
        )
        return auth_url

    def exchange_code(self, code: str, user_email: str, redirect_uri: str = "") -> Credentials:
        """Exchange authorization code for credentials and save.

        Args:
            code: Authorization code from Google callback.
            user_email: User's email (from state parameter).
            redirect_uri: Dynamic redirect URI (must match the one used in get_auth_url).

        Returns:
            The obtained Credentials.
        """
        uri = redirect_uri or self.settings.google_oauth_redirect_uri
        flow = Flow.from_client_config(
            self._client_config(),
            scopes=SCOPES,
            redirect_uri=uri,
        )
        flow.fetch_token(code=code)
        creds = flow.credentials

        # Fetch Google account email via Gmail API
        google_email = ""
        try:
            import httpx
            resp = httpx.get(
                "https://gmail.googleapis.com/gmail/v1/users/me/profile",
                headers={"Authorization": f"Bearer {creds.token}"},
                timeout=300,
            )
            if resp.status_code == 200:
                google_email = resp.json().get("emailAddress", "")
        except Exception:
            pass

        self._save_credentials(user_email, creds, google_email=google_email)
        logger.info("token_saved")
        return creds

    def revoke_credentials(self, user_email: str) -> bool:
        """Delete stored credentials for a user.

        Args:
            user_email: User's email address.

        Returns:
            True if deleted, False if not found.
        """
        token_path = self._token_path(user_email)
        if token_path.exists():
            token_path.unlink()
            logger.info("token_revoked")
            return True
        return False

    def _save_credentials(
        self, user_email: str, creds: Credentials, *, google_email: str = ""
    ) -> None:
        """Save credentials to JSON file."""
        token_path = self._token_path(user_email)
        token_data = {
            "token": creds.token,
            "refresh_token": creds.refresh_token,
            "token_uri": creds.token_uri,
            "client_id": creds.client_id,
            "client_secret": creds.client_secret,
            "scopes": list(creds.scopes) if creds.scopes else SCOPES,
        }
        if google_email:
            token_data["google_email"] = google_email
        token_path.write_text(json.dumps(token_data), encoding="utf-8")
