"""Single-use OAuth state tokens bound to the current application user."""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

import jwt

from app.config import get_settings, validate_jwt_secret
from app.db.mariadb import execute


PURPOSE = "gws_oauth"
TTL = timedelta(minutes=10)

_DDL = """
CREATE TABLE IF NOT EXISTS google_oauth_states (
    nonce_hash CHAR(64) PRIMARY KEY,
    user_id INT NOT NULL,
    user_email VARCHAR(320) NOT NULL,
    expires_at DATETIME NOT NULL,
    used_at DATETIME NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_oauth_state_expiry (expires_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""


def ensure_oauth_state_table() -> None:
    """Create the state table and remove safely expired nonces."""
    execute(_DDL)
    execute("DELETE FROM google_oauth_states WHERE expires_at < DATE_SUB(NOW(), INTERVAL 1 DAY)")


def issue_state(user_id: int, user_email: str, now: datetime | None = None) -> str:
    """Issue a signed state token and persist its one-time nonce server-side."""
    current = now or datetime.now(timezone.utc)
    nonce = secrets.token_urlsafe(32)
    nonce_hash = hashlib.sha256(nonce.encode()).hexdigest()
    expires = current + TTL
    execute(
        "INSERT INTO google_oauth_states (nonce_hash,user_id,user_email,expires_at) VALUES (%s,%s,%s,%s)",
        (nonce_hash, int(user_id), user_email, expires.replace(tzinfo=None)),
    )
    payload = {
        "purpose": PURPOSE,
        "user_id": int(user_id),
        "email": user_email,
        "nonce": nonce,
        "iat": current,
        "exp": expires,
    }
    secret = validate_jwt_secret(get_settings().jwt_secret_key)
    return jwt.encode(payload, secret, algorithm="HS256")


def consume_state(token: str, current_user_id: int, now: datetime | None = None) -> dict[str, object]:
    """Validate and atomically consume a state token for its owning JWT user."""
    secret = validate_jwt_secret(get_settings().jwt_secret_key)
    payload = jwt.decode(token, secret, algorithms=["HS256"], options={"verify_exp": now is None})
    if now is not None and datetime.fromtimestamp(payload["exp"], timezone.utc) < now:
        raise ValueError("expired oauth state")
    if payload.get("purpose") != PURPOSE or int(payload.get("user_id", 0)) != int(current_user_id):
        raise ValueError("oauth state user mismatch")

    nonce_hash = hashlib.sha256(str(payload["nonce"]).encode()).hexdigest()
    changed = execute(
        "UPDATE google_oauth_states SET used_at=NOW() "
        "WHERE nonce_hash=%s AND user_id=%s AND user_email=%s "
        "AND used_at IS NULL AND expires_at >= NOW()",
        (nonce_hash, int(current_user_id), str(payload["email"])),
    )
    if changed != 1:
        raise ValueError("oauth state already used or expired")
    return payload
