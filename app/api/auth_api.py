"""Authentication endpoints: signup, signin, me, logout.

Uses MariaDB for user storage with AD-linked department+name login.
"""

import asyncio
import time
from datetime import datetime, timedelta, timezone

import bcrypt as _bcrypt
import jwt
import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel

from app.api.auth_middleware import get_current_user
from app.config import get_settings
from app.db.mariadb import fetch_all, fetch_one, execute, execute_lastid
from app.db.models import User

logger = structlog.get_logger(__name__)

auth_api_router = APIRouter(prefix="/api/auth", tags=["auth"])

_ALGORITHM = "HS256"
_TOKEN_EXPIRE_DAYS = 7
_ALL_MODELS = "skin1004-Analysis"

# ── AD user cache (avoid DB hit on every keystroke) ──
_ad_cache: list[dict] = []
_ad_cache_ts: float = 0
_AD_CACHE_TTL = 300  # 5 minutes


# ── Async DB wrappers ──

async def _db_fetch_all(sql: str, params: tuple = ()) -> list[dict]:
    return await asyncio.to_thread(fetch_all, sql, params)


async def _db_fetch_one(sql: str, params: tuple = ()):
    return await asyncio.to_thread(fetch_one, sql, params)


async def _db_execute(sql: str, params: tuple = ()) -> int:
    return await asyncio.to_thread(execute, sql, params)


async def _db_execute_lastid(sql: str, params: tuple = ()) -> int:
    return await asyncio.to_thread(execute_lastid, sql, params)


# ── Schemas ──

class SignupRequest(BaseModel):
    department: str
    name: str
    password: str


class SigninRequest(BaseModel):
    department: str
    name: str
    password: str


class UserResponse(BaseModel):
    id: int
    email: str
    name: str
    department: str
    role: str
    allowed_models: list[str]


# ── Helpers ──

def _resolve_models(role: str, allowed_models: str | None) -> list[str]:
    if role == "admin":
        return [m.strip() for m in _ALL_MODELS.split(",") if m.strip()]
    raw = allowed_models or ""
    models = [m.strip() for m in raw.split(",") if m.strip()]
    return models if models else ["skin1004-Analysis"]


def _user_response(user_row: dict) -> dict:
    """Build UserResponse dict from a joined users+ad_users row."""
    return {
        "id": user_row["id"],
        "email": user_row.get("ad_email") or user_row.get("email") or "",
        "name": user_row.get("ad_name") or user_row.get("display_name") or "",
        "department": user_row.get("department") or "",
        "role": user_row["role"],
        "allowed_models": _resolve_models(user_row["role"], user_row.get("allowed_models")),
    }


def _create_token(user_id: int, email: str = "", brand_filter: str = "", role: str = "user") -> str:
    settings = get_settings()
    payload = {
        "user_id": user_id,
        "email": email,
        "exp": datetime.now(timezone.utc) + timedelta(days=_TOKEN_EXPIRE_DAYS),
        "brand_filter": brand_filter,
        "role": role,
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=_ALGORITHM)


def _lookup_brand_filter(user_id: int) -> str:
    """Look up brand_filter from user's group membership."""
    row = fetch_one(
        "SELECT g.brand_filter FROM users u "
        "LEFT JOIN user_groups ug ON u.ad_user_id = ug.ad_user_id "
        "LEFT JOIN access_groups g ON ug.group_id = g.id AND g.brand_filter IS NOT NULL "
        "WHERE u.id = %s LIMIT 1",
        (user_id,),
    )
    return (row.get("brand_filter") or "") if row else ""


def _set_cookie(response: Response, token: str):
    settings = get_settings()
    response.set_cookie(
        key="token",
        value=token,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        max_age=_TOKEN_EXPIRE_DAYS * 86400,
        path="/",
    )


# ── Public endpoints (no auth required, for login form) ──

@auth_api_router.get("/departments")
async def list_departments():
    """List all departments with user counts (from cache)."""
    cache = await _get_ad_cache()
    counts: dict[str, int] = {}
    for u in cache:
        d = u.get("department") or ""
        if d:
            counts[d] = counts.get(d, 0) + 1
    return [{"department": k, "cnt": v} for k, v in sorted(counts.items())]


@auth_api_router.get("/users-by-dept")
async def list_users_by_department(
    dept: str = Query(..., description="Department name (exact match)")
):
    """List AD users in a department (for login form name selector)."""
    users = await _db_fetch_all("""
        SELECT ad.id, ad.display_name, ad.email,
               CASE WHEN u.id IS NOT NULL THEN 1 ELSE 0 END as registered
        FROM ad_users ad
        LEFT JOIN users u ON ad.id = u.ad_user_id
        WHERE ad.is_active = 1 AND ad.department = %s
        ORDER BY ad.display_name
    """, (dept,))
    return users


async def _get_ad_cache() -> list[dict]:
    """Return cached AD user list, refresh if stale."""
    global _ad_cache, _ad_cache_ts
    now = time.time()
    if _ad_cache and (now - _ad_cache_ts) < _AD_CACHE_TTL:
        return _ad_cache
    rows = await _db_fetch_all("""
        SELECT ad.id,
               COALESCE(u.display_name, ad.display_name) AS display_name,
               ad.display_name AS ad_name,
               ad.username,
               COALESCE(ad.email, u.email) AS email,
               ad.department
        FROM ad_users ad
        LEFT JOIN users u ON ad.id = u.ad_user_id COLLATE utf8mb4_unicode_ci
        WHERE ad.is_active = 1 AND ad.department IS NOT NULL AND ad.department != ''
        ORDER BY COALESCE(u.display_name, ad.display_name), ad.department
    """)
    _ad_cache = rows
    _ad_cache_ts = now
    logger.info("ad_cache_refreshed", count=len(rows))
    return _ad_cache


@auth_api_router.get("/search-name")
async def search_by_name(
    name: str = Query(..., min_length=1, description="Name to search")
):
    """Find AD users by name (searches display_name, ad_name, username)."""
    cache = await _get_ad_cache()
    q = name.lower()
    results = []
    for u in cache:
        display = (u.get("display_name") or "").lower()
        ad_name = (u.get("ad_name") or "").lower()
        uname = (u.get("username") or "").lower()
        if q in display or q in ad_name or q in uname:
            results.append(u)
            if len(results) >= 20:
                break
    return results


# ── Auth endpoints ──

@auth_api_router.post("/signup")
async def signup(req: SignupRequest, response: Response):
    """Create a new user account linked to an AD user."""
    if len(req.password) < 4:
        raise HTTPException(status_code=400, detail="비밀번호는 4자 이상이어야 합니다")

    # Find AD user by department + name
    ad_user = await _db_fetch_one(
        "SELECT id, display_name, email, department FROM ad_users "
        "WHERE is_active = 1 AND department = %s AND display_name = %s",
        (req.department, req.name),
    )
    if not ad_user:
        raise HTTPException(status_code=404, detail="해당 부서/이름의 AD 사용자를 찾을 수 없습니다")

    # Check if already registered
    existing = await _db_fetch_one(
        "SELECT id FROM users WHERE ad_user_id = %s", (ad_user["id"],)
    )
    if existing:
        raise HTTPException(status_code=409, detail="이미 등록된 사용자입니다. 로그인해 주세요.")

    # Hash password
    pw_hash = _bcrypt.hashpw(req.password.encode(), _bcrypt.gensalt()).decode()

    # Use AD email, or generate unique placeholder for users without email
    user_email = ad_user["email"] or f"ad_{ad_user['id']}@noemail.local"

    # Create user
    user_id = await _db_execute_lastid(
        "INSERT INTO users (email, password_hash, display_name, role, allowed_models, ad_user_id) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (user_email, pw_hash, ad_user["display_name"], "user", "skin1004-Analysis", ad_user["id"]),
    )

    bf = await asyncio.to_thread(_lookup_brand_filter, user_id)
    token = _create_token(user_id, ad_user.get("email") or "", brand_filter=bf, role="user")
    _set_cookie(response, token)

    logger.info("user_signup", name=req.name, department=req.department, user_id=user_id)
    return {
        "id": user_id,
        "email": ad_user.get("email") or "",
        "name": ad_user["display_name"],
        "department": ad_user["department"],
        "role": "user",
        "allowed_models": ["skin1004-Analysis"],
    }


@auth_api_router.post("/signin")
async def signin(req: SigninRequest, response: Response):
    """Sign in with department + name + password."""
    # Find AD user
    ad_user = await _db_fetch_one(
        "SELECT id, display_name, email, department FROM ad_users "
        "WHERE is_active = 1 AND department = %s AND display_name = %s",
        (req.department, req.name),
    )
    if not ad_user:
        raise HTTPException(status_code=401, detail="사용자를 찾을 수 없습니다")

    # Find registered user
    user = await _db_fetch_one(
        "SELECT id, password_hash, role, allowed_models FROM users WHERE ad_user_id = %s",
        (ad_user["id"],),
    )
    if not user:
        raise HTTPException(status_code=401, detail="등록되지 않은 사용자입니다. 회원가입을 먼저 해주세요.")

    # Verify password
    if not _bcrypt.checkpw(req.password.encode(), user["password_hash"].encode()):
        raise HTTPException(status_code=401, detail="비밀번호가 일치하지 않습니다")

    # Update last_login
    await _db_execute(
        "UPDATE users SET last_login = NOW() WHERE id = %s", (user["id"],)
    )

    bf = await asyncio.to_thread(_lookup_brand_filter, user["id"])
    token = _create_token(user["id"], ad_user.get("email") or "", brand_filter=bf, role=user.get("role", "user"))
    _set_cookie(response, token)

    logger.info("user_signin", name=req.name, department=req.department)
    return {
        "id": user["id"],
        "email": ad_user.get("email") or "",
        "name": ad_user["display_name"],
        "department": ad_user["department"],
        "role": user["role"],
        "allowed_models": _resolve_models(user["role"], user.get("allowed_models")),
    }


@auth_api_router.get("/me")
async def me(user: User = Depends(get_current_user)):
    """Get current authenticated user."""
    # brand_filters = what the dropdown shows
    # Admin: all groups (can choose any filter), no personal filter
    # Non-admin: only their own groups (auto-enforced)
    if user.role == "admin":
        all_groups = await _db_fetch_all(
            "SELECT name, brand_filter FROM access_groups WHERE brand_filter IS NOT NULL"
        )
        brand_filters = [{"group": r["name"], "brands": r["brand_filter"]} for r in all_groups]
        my_brand_filters = []
    else:
        my_brand_filters = []
        if user.ad_user_id:
            rows = await _db_fetch_all(
                "SELECT g.name, g.brand_filter FROM access_groups g "
                "JOIN user_groups ug ON g.id = ug.group_id "
                "WHERE ug.ad_user_id = %s AND g.brand_filter IS NOT NULL",
                (user.ad_user_id,),
            )
            my_brand_filters = [{"group": r["name"], "brands": r["brand_filter"]} for r in rows]
        brand_filters = my_brand_filters

    return {
        "id": user.id,
        "email": user.email,
        "name": user.name,
        "department": user.department,
        "role": user.role,
        "allowed_models": _resolve_models(user.role, user.allowed_models),
        "brand_filters": brand_filters,
        "my_brand_filter": my_brand_filters[0]["brands"] if my_brand_filters else None,
    }


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


@auth_api_router.post("/change-password")
async def change_password(req: ChangePasswordRequest, user: User = Depends(get_current_user)):
    """Change password for current user."""
    if len(req.new_password) < 4:
        raise HTTPException(status_code=400, detail="새 비밀번호는 4자 이상이어야 합니다")

    # Get current password hash
    row = await _db_fetch_one(
        "SELECT password_hash FROM users WHERE id = %s", (user.id,)
    )
    if not row:
        raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다")

    # Verify current password
    if not _bcrypt.checkpw(req.current_password.encode(), row["password_hash"].encode()):
        raise HTTPException(status_code=401, detail="현재 비밀번호가 일치하지 않습니다")

    # Hash and update
    new_hash = _bcrypt.hashpw(req.new_password.encode(), _bcrypt.gensalt()).decode()
    await _db_execute(
        "UPDATE users SET password_hash = %s, updated_at = NOW() WHERE id = %s",
        (new_hash, user.id),
    )

    logger.info("password_changed", user_id=user.id, name=user.name)
    return {"ok": True}


@auth_api_router.post("/logout")
async def logout(response: Response):
    """Clear auth cookie."""
    response.delete_cookie(key="token", path="/")
    return {"ok": True}

