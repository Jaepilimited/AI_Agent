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
from pathlib import Path
from typing import Optional

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

    def get_credentials(self, user_email: str) -> Optional[Credentials]:
        """Load credentials for a user, trying local file first, then Open WebUI DB.

        Args:
            user_email: User's email address.

        Returns:
            Valid Credentials, or None if not authenticated.
        """
        # Try 1: Local token file (from separate auth flow)
        creds = self._get_credentials_from_file(user_email)
        if creds is not None:
            return creds

        # Try 2: Open WebUI's OAuth session DB
        creds = self._get_credentials_from_openwebui(user_email)
        if creds is not None:
            return creds

        return None

    def _get_credentials_from_file(self, user_email: str) -> Optional[Credentials]:
        """Load credentials from local token file."""
        token_path = self._token_path(user_email)
        if not token_path.exists():
            return None

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
                logger.info("token_refreshed", user_email=user_email, source="file")
                return creds
            if creds.valid:
                return creds
            return None
        except Exception as e:
            logger.error("token_load_failed", user_email=user_email, error=str(e))
            return None

    def _get_credentials_from_openwebui(self, user_email: str) -> Optional[Credentials]:
        """Load credentials from Open WebUI's Docker container via docker exec.

        Open WebUI stores OAuth tokens encrypted with Fernet in its oauth_session table.
        We extract the token via docker exec to avoid SQLite locking issues.
        """
        secret_key = self.settings.openwebui_secret_key
        if not secret_key:
            return None

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
                capture_output=True, text=True, timeout=10,
            )
            output = result.stdout.strip()
            if not output or output in ("NO_USER", "NO_SESSION", "DECRYPT_FAIL"):
                if output:
                    logger.warning("openwebui_token_extract", result=output, user_email=user_email)
                return None

            token_data = json.loads(output)

            access_token = token_data.get("access_token")
            refresh_token = token_data.get("refresh_token")

            if not access_token:
                logger.warning("openwebui_token_no_access", user_email=user_email)
                return None

            # Check if token has GWS scopes
            scope_str = token_data.get("scope", "")
            has_gws_scopes = any(
                s in scope_str
                for s in ["gmail.readonly", "calendar.readonly", "drive.readonly"]
            )
            if not has_gws_scopes:
                logger.warning(
                    "openwebui_token_missing_gws_scopes",
                    user_email=user_email,
                    scopes=scope_str,
                )
                return None

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
                    logger.info("openwebui_token_refreshed", user_email=user_email)
                except Exception as e:
                    logger.warning("openwebui_token_refresh_failed", error=str(e))
                    return None

            if creds.valid:
                logger.info("openwebui_token_loaded", user_email=user_email)
                return creds

            # Token might still be valid even if expired flag is uncertain
            # Try returning it and let the API call determine
            if access_token:
                logger.info("openwebui_token_loaded_unchecked", user_email=user_email)
                return creds

            return None

        except Exception as e:
            logger.error(
                "openwebui_token_error", user_email=user_email, error=str(e)
            )
            return None

    def get_auth_url(self, user_email: str, redirect_uri: str = "") -> str:
        """Generate Google OAuth2 authorization URL.

        Args:
            user_email: User's email for state parameter.
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
            state=user_email,
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
                timeout=5,
            )
            if resp.status_code == 200:
                google_email = resp.json().get("emailAddress", "")
        except Exception:
            pass

        self._save_credentials(user_email, creds, google_email=google_email)
        logger.info("token_saved", user_email=user_email, google_email=google_email)
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
            logger.info("token_revoked", user_email=user_email)
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
