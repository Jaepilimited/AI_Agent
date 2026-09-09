"""JWT cookie-based authentication dependency for FastAPI (MariaDB)."""

import asyncio
import time
from typing import Optional

import jwt
from fastapi import HTTPException, Request

from app.config import ALL_MODELS, get_settings
from app.core.session_auth import decode_session, LOGIN_REQUIRED_HEADERS, LOGIN_REQUIRED_MESSAGE
from app.db.mariadb import fetch_one
from app.db.models import User

# Same TTL convention as the AD user cache — bounds how stale a role/
# permission change can be for an already-cached user without needing
# explicit cache invalidation on every users/directory_users write path.
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

GROUP_ASSIGNMENT_MESSAGE = "회사 계정이 연결되었습니다. 관리자가 데이터 조회 그룹을 배정하면 질문과 보고서를 이용할 수 있습니다."


def _requires_data_group(path: str) -> bool:
    return path == "/v1/chat/completions" or any(
        path == prefix or path.startswith(prefix + "/")
        for prefix in ("/api/reports", "/api/sql-results", "/api/saved-questions")
    )


def _extract_user_id(request: Request) -> int:
    """Extract user_id from JWT cookie. Raises 401 on failure."""
    token = request.cookies.get("token")
    if not token:
        raise HTTPException(status_code=401, detail=LOGIN_REQUIRED_MESSAGE, headers=LOGIN_REQUIRED_HEADERS)

    settings = get_settings()
    try:
        payload = decode_session(token, settings, request=request)
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail=LOGIN_REQUIRED_MESSAGE, headers=LOGIN_REQUIRED_HEADERS)

    request.state.session_claims = payload
    # Wave 1: Cache brand_filter and role from JWT claims
    request.state.jwt_brand_filter = payload.get("brand_filter", "")
    request.state.jwt_role = payload.get("role", "")

    return payload["user_id"]


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
            "u.must_change_password, u.is_active AS account_active, a.is_active AS directory_active, "
            "(u.requires_group_assignment AND NOT EXISTS "
            "(SELECT 1 FROM user_groups ug JOIN access_groups g ON g.id=ug.group_id "
            "WHERE ug.ad_user_id=u.ad_user_id AND g.brand_filter IS NOT NULL "
            "AND g.brand_filter<>'')) AS requires_group_assignment, "
            "a.display_name as ad_name, a.email as ad_email, a.department, "
            "COALESCE(a.can_view_visitor_analytics, 0) AS can_view_visitor_analytics "
            "FROM users u LEFT JOIN directory_users a ON u.ad_user_id = a.id "
            "WHERE u.id = %s",
            (user_id,),
        )
        if not row:
            raise HTTPException(status_code=401, detail=LOGIN_REQUIRED_MESSAGE, headers=LOGIN_REQUIRED_HEADERS)
        if row.get("account_active") == 0 or row.get("directory_active") == 0:
            raise HTTPException(status_code=403, detail="이용이 중지된 계정입니다. 관리자에게 문의해 주세요.")

        user = User(
            id=row["id"],
            email=row.get("ad_email") or row.get("email") or "",
            name=row.get("ad_name") or row.get("display_name") or "",
            department=row.get("department") or "",
            role=row["role"],
            allowed_models=row.get("allowed_models") or ALL_MODELS,
            ad_user_id=row.get("ad_user_id"),
            can_view_visitor_analytics=bool(row.get("can_view_visitor_analytics")),
            must_change_password=bool(row.get("must_change_password")),
            requires_group_assignment=bool(row.get("requires_group_assignment")),
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

    if user.role != "admin" and user.requires_group_assignment and _requires_data_group(request.url.path):
        raise HTTPException(status_code=403, detail=GROUP_ASSIGNMENT_MESSAGE)

    return user


def invalidate_user_cache(user_id: int | None = None) -> None:
    """Drop a cached User so the next request re-reads it from MariaDB instead of
    serving up to `_USER_CACHE_TTL` stale seconds of role/permission/
    must_change_password state. Call after any write that changes what's cached —
    e.g. clearing must_change_password on a successful password change."""
    if user_id is None:
        _user_cache.clear()
    else:
        _user_cache.pop(user_id, None)


async def get_optional_user(request: Request) -> Optional[User]:
    """Like get_current_user but returns None instead of 401."""
    try:
        return await get_current_user(request)
    except HTTPException:
        return None
