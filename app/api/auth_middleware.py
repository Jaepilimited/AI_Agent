"""JWT cookie-based authentication dependency for FastAPI (MariaDB)."""

import asyncio
import time
from typing import Optional

import jwt
from fastapi import HTTPException, Request

from app.config import ALL_MODELS, get_settings, validate_jwt_secret
from app.db.mariadb import fetch_one
from app.db.models import User

_ALGORITHM = "HS256"

# Same TTL convention as the AD user cache — bounds how stale a role/
# permission change can be for an already-cached user without needing
# explicit cache invalidation on every users/ad_users write path.
_USER_CACHE_TTL = 60.0
_user_cache: dict[int, tuple[User, float]] = {}

# 관리자가 비밀번호를 초기화한 사용자는 로그인은 되지만 새 비밀번호를 정하기
# 전까지 그 외 아무것도 못 한다 — 여기 없는 모든 경로가 막힌다. 프론트가 아니라
# 이 관문(모든 인증 경로가 거치는 get_current_user)에서 막아야 API 를 직접
# 호출해 우회하는 것도 막는다.
_MUST_CHANGE_PASSWORD_EXEMPT_PATHS = {
    "/api/auth/me",
    "/api/auth/change-password",
    "/api/auth/logout",
}


def _extract_user_id(request: Request) -> int:
    """Extract user_id from JWT cookie. Raises 401 on failure."""
    token = request.cookies.get("token")
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")

    settings = get_settings()
    try:
        payload = jwt.decode(
            token,
            validate_jwt_secret(settings.jwt_secret_key),
            algorithms=[_ALGORITHM],
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")

    user_id = payload.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid token payload")

    # Wave 1: Cache brand_filter and role from JWT claims
    request.state.jwt_brand_filter = payload.get("brand_filter", "")
    request.state.jwt_role = payload.get("role", "")

    return int(user_id)


async def get_current_user(request: Request) -> User:
    """Extract and validate JWT from httpOnly cookie, return User from MariaDB."""
    user_id = _extract_user_id(request)

    cached = _user_cache.get(user_id)
    if cached and (time.monotonic() - cached[1]) < _USER_CACHE_TTL:
        user = cached[0]
    else:
        row = await asyncio.to_thread(
            fetch_one,
            "SELECT u.id, u.email, u.display_name, u.role, u.allowed_models, u.ad_user_id, "
            "u.must_change_password, "
            "a.display_name as ad_name, a.email as ad_email, a.department "
            "FROM users u LEFT JOIN ad_users a ON u.ad_user_id = a.id "
            "WHERE u.id = %s",
            (user_id,),
        )
        if not row:
            raise HTTPException(status_code=401, detail="User not found")

        user = User(
            id=row["id"],
            email=row.get("ad_email") or row.get("email") or "",
            name=row.get("ad_name") or row.get("display_name") or "",
            department=row.get("department") or "",
            role=row["role"],
            allowed_models=row.get("allowed_models") or ALL_MODELS,
            ad_user_id=row.get("ad_user_id"),
            must_change_password=bool(row.get("must_change_password")),
        )
        _user_cache[user_id] = (user, time.monotonic())

    # Store on request.state for downstream use
    request.state.user_email = user.email
    request.state.user_id = user.id

    if user.must_change_password and request.url.path not in _MUST_CHANGE_PASSWORD_EXEMPT_PATHS:
        raise HTTPException(
            status_code=403,
            detail="비밀번호가 초기화되었습니다. 계속하려면 먼저 비밀번호를 변경해 주세요.",
        )

    return user


def invalidate_user_cache(user_id: int) -> None:
    """Drop a cached User so the next request re-reads it from MariaDB instead of
    serving up to `_USER_CACHE_TTL` stale seconds of role/permission/
    must_change_password state. Call after any write that changes what's cached —
    e.g. clearing must_change_password on a successful password change."""
    _user_cache.pop(user_id, None)


async def get_optional_user(request: Request) -> Optional[User]:
    """Like get_current_user but returns None instead of 401."""
    try:
        return await get_current_user(request)
    except HTTPException:
        return None
