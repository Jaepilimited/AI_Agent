"""Validate Cella sessions without confusing account linkage with authentication."""

from datetime import datetime, timezone
from uuid import UUID

import jwt

from app.config import get_settings, validate_jwt_secret

LOGIN_REQUIRED_HEADERS = {"X-Cella-Auth": "login-required", "Cache-Control": "no-store"}
LOGIN_REQUIRED_MESSAGE = "회사 계정(Entra ID)으로 다시 로그인해 주세요."
_MONITORS = {"golden_runner", "self_check"}


def _uuid(value) -> str:
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError, AttributeError) as exc:
        raise jwt.InvalidTokenError("Invalid Entra identity") from exc


def decode_session(token: str, settings=None, *, request=None) -> dict:
    """Only the verified OIDC callback can establish Entra authentication proof.

    Existing cookies have no proof and cannot be upgraded from a database link.
    Internal monitors use a separate, narrowly scoped token on direct loopback.
    """
    settings = settings or get_settings()
    payload = jwt.decode(
        token, validate_jwt_secret(settings.jwt_secret_key), algorithms=["HS256"],
        options={"require": ["exp", "user_id"]},
    )
    if type(payload["user_id"]) is not int or payload["user_id"] <= 0:
        raise jwt.InvalidTokenError("Invalid session user")

    purpose = payload.get("purpose")
    provider = payload.get("auth_provider")
    if purpose == "service":
        issued, expires = payload.get("iat"), payload.get("exp")
        if (
            provider != "internal_monitor" or payload.get("service") not in _MONITORS
            or payload.get("scope") != "chat:probe"
            or type(issued) not in (int, float) or type(expires) not in (int, float)
            or not 0 < expires - issued <= 7200
            or request is None or request.method != "POST"
            or request.url.path != "/v1/chat/completions"
            or not request.client or request.client.host not in {"127.0.0.1", "::1"}
            or any(k.lower() == "forwarded" or k.lower().startswith("x-forwarded-")
                   for k in request.headers)
        ):
            raise jwt.InvalidTokenError("Service session is outside its scope")
        return payload

    if purpose not in (None, "session"):
        raise jwt.InvalidTokenError("Token is not a login session")
    if provider == "entra":
        if purpose != "session":
            raise jwt.InvalidTokenError("Missing session purpose")
        if _uuid(payload.get("entra_tid")) != _uuid(getattr(settings, "entra_tenant_id", "")):
            raise jwt.InvalidTokenError("Entra tenant mismatch")
        _uuid(payload.get("entra_oid"))
    elif provider not in (None, "password") or not getattr(settings, "password_login_enabled", False):
        raise jwt.InvalidTokenError("Entra authentication is required")
    return payload


def create_service_token(user_id: int, email: str, role: str = "user", *,
                         service: str, lifetime_seconds: int = 900) -> str:
    """Issue a server monitor credential; it cannot open or refresh a browser session."""
    if service not in _MONITORS or not 0 < lifetime_seconds <= 7200:
        raise ValueError("Invalid monitor token scope or lifetime")
    now = int(datetime.now(timezone.utc).timestamp())
    payload = {
        "purpose": "service", "auth_provider": "internal_monitor", "scope": "chat:probe",
        "service": service, "user_id": user_id, "email": email, "role": role,
        "brand_filter": "", "iat": now, "exp": now + lifetime_seconds,
    }
    return jwt.encode(payload, validate_jwt_secret(get_settings().jwt_secret_key), algorithm="HS256")
