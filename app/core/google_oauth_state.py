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
    """Create the state table and remove rows that no longer serve replay defense."""
    execute(_DDL)
    _cleanup_states()


def _cleanup_states() -> None:
    """Delete identity-bearing rows once expired or consumed."""
    execute(
        "DELETE FROM google_oauth_states "
        "WHERE expires_at < UTC_TIMESTAMP() OR used_at IS NOT NULL"
    )


def issue_state(user_id: int, user_email: str, now: datetime | None = None,
                origin: str = "") -> str:
    """Issue a signed state token and persist its one-time nonce server-side.

    ``origin`` 은 사용자가 보던 주소(``https://ai.cravercorp.internal``)다. 구글이
    등록된 콜백(``…nip.io``)으로 돌려보내면 그곳엔 쿠키가 없으므로, 콜백이 이
    값을 보고 쿠키가 있는 곳으로 한 번 되돌린다. 서명 안에 두어 위조할 수 없다.
    """
    _cleanup_states()
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
    if origin:
        payload["origin"] = origin
    secret = validate_jwt_secret(get_settings().jwt_secret_key)
    return jwt.encode(payload, secret, algorithm="HS256")


def peek_origin(token: str) -> str:
    """서명·만료·용도가 맞는 state 의 ``origin`` 만 읽는다 (소모하지 않는다).

    쿠키 없는 콜백이 되돌아갈 곳을 고를 때 쓴다 — 사용자가 아직 누군지 모르는
    단계라 소모는 쿠키가 실린 두 번째 요청이 한다.
    """
    try:
        payload = jwt.decode(token or "", validate_jwt_secret(get_settings().jwt_secret_key),
                             algorithms=["HS256"])
    except Exception:   # 위조·만료·서명키 문제 모두 "되돌아갈 곳 없음" 이다
        return ""
    if payload.get("purpose") != PURPOSE:
        return ""
    return str(payload.get("origin") or "")


def consume_state(token: str, current_user_id: int, now: datetime | None = None) -> dict[str, object]:
    """Validate and atomically consume a state token for its owning JWT user."""
    secret = validate_jwt_secret(get_settings().jwt_secret_key)
    payload = jwt.decode(token, secret, algorithms=["HS256"], options={"verify_exp": now is None})
    _cleanup_states()
    if now is not None and datetime.fromtimestamp(payload["exp"], timezone.utc) < now:
        raise ValueError("expired oauth state")
    if payload.get("purpose") != PURPOSE or int(payload.get("user_id", 0)) != int(current_user_id):
        raise ValueError("oauth state user mismatch")

    nonce_hash = hashlib.sha256(str(payload["nonce"]).encode()).hexdigest()
    changed = execute(
        "DELETE FROM google_oauth_states WHERE nonce_hash=%s AND user_id=%s AND user_email=%s "
        "AND used_at IS NULL AND expires_at >= UTC_TIMESTAMP()",
        (nonce_hash, int(current_user_id), str(payload["email"])),
    )
    if changed != 1:
        raise ValueError("oauth state already used or expired")
    return payload
