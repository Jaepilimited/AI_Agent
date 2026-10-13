"""Authentication endpoints: signup, signin, me, logout.

Uses MariaDB for user storage with AD-linked department+name login.
"""

import asyncio
import time
from collections import OrderedDict
from datetime import datetime, timedelta, timezone

import bcrypt as _bcrypt
import jwt
import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from app.api.auth_middleware import get_current_user, invalidate_user_cache
from app.config import ALL_MODELS, get_settings, validate_jwt_secret
from app.core.visitor_access import can_view_visitor_analytics
from app.db.mariadb import fetch_all, fetch_one, execute, execute_lastid
from app.db.models import User

logger = structlog.get_logger(__name__)

auth_api_router = APIRouter(prefix="/api/auth", tags=["auth"])

_ALGORITHM = "HS256"
_TOKEN_EXPIRE_DAYS = 7
_ALL_MODELS = ALL_MODELS

# ── 로컬 ID/PW 로그인 차단 (기본 꺼짐 — 2026-09-08) ──────────────────────────
# 회사 계정(Entra ID)으로 옮기면서 로컬 비밀번호 경로를 닫았다.
# ⛔ **코드를 지우지 않았다.** 되돌릴 길이 30초 안에 있어야 한다 —
#    WAS 의 `.env` 에 `PASSWORD_LOGIN_ENABLED=true` 를 넣고 재기동하면 그대로
#    살아난다 (`app/config.py`의 `password_login_enabled` 주석 참조).
# ⚠️ 관문은 **DB 조회·bcrypt 해싱보다 먼저** 선다. 뒤에 두면 꺼진 경로가
#    계속 사용자 조회와 해시 계산을 태우고, 응답 시간으로 존재 여부가 샌다.
PASSWORD_LOGIN_DISABLED_MESSAGE = (
    "아이디·비밀번호 로그인은 더 이상 사용하지 않습니다. 회사 계정으로 로그인해 주세요."
)


def password_login_enabled() -> bool:
    """로컬 ID/PW 경로가 켜져 있나. 설정 하나가 단일 소스다."""
    return bool(getattr(get_settings(), "password_login_enabled", False))


def require_password_login() -> None:
    """꺼져 있으면 **읽을 수 있는 한국어**로 403 을 준다.

    ⛔ 맨 에러(500·404)로 막지 마라 — 못 들어오는 사람이 무엇을 해야 하는지
       알 수 없어 관리자에게 "로그인이 고장났다" 로 접수된다.
    """
    if not password_login_enabled():
        raise HTTPException(status_code=403, detail=PASSWORD_LOGIN_DISABLED_MESSAGE)

# ── AD user cache (avoid DB hit on every keystroke) ──
_ad_cache: list[dict] = []
_ad_cache_ts: float = 0
_AD_CACHE_TTL = 300  # 5 minutes

# ── On-demand AD resync fallback ──
# The scheduled sync only runs nightly at 22:00 (SKIN1004-AD-Sync-Daily). A brand-new
# hire whose AD account was created *today* is invisible to signup/signin until then,
# which turns into a same-day "I can't find my name / it says user not found" incident
# every time someone onboards mid-day. Self-heal: when a lookup against the local
# directory_users cache/table comes up empty, kick off one live AD fetch + upsert in the
# background before answering the failing request.
#
# ⛔ This must NEVER be awaited on the request path. A live full resync (fetch_ad_users
#    + sync_to_db over ~362 accounts) measured ~135.9s in production (2026-09-01).
#    Probed from the WAS afterwards: 10.1.150.5 -> 172.16.1.13 is CLOSED on BOTH 389
#    and **636**, so those 135s were a firewall timeout and this self-heal has NEVER
#    actually healed anything on the WAS — it cannot reach AD at all. (Same probe from
#    the APP host 10.1.150.105: both ports OPEN — that is why the nightly sync lives
#    there.) ⚠️ Name 636 when asking IT to open access: `scripts/sync_ad_users.py`
#    connects with `port=636, use_ssl=True` (LDAPS). Opening only 389 would look like
#    a fix and change nothing. Leaving it wired anyway: it is harmless
#    off the request path, and it starts working the day LDAPS is opened (or if this
#    ever runs somewhere that can reach AD). A user who picked the wrong team was
#    made to stare at a spinner for over two minutes for what should be a ~1s "not
#    found." The resync can only ever help the *next* attempt (the user retries once
#    they're actually in AD, or a real same-day hire tries again a bit later) — this
#    request's own miss has to answer fast regardless of whether the resync helps or not.
_resync_in_progress = False
_last_ad_resync_finished: float = 0
_AD_RESYNC_COOLDOWN = 120  # seconds since the last resync FINISHED (not started)


def _trigger_ad_fallback_resync() -> None:
    """Fire-and-forget: schedule a background AD resync if one isn't already running
    and the cooldown (measured from when the *last one finished*) has elapsed.

    Never awaited by callers — call this and move on. The previous version stamped
    the cooldown when a resync *started*, so a resync that itself takes longer than
    the cooldown (measured: 135s sync vs a 120s cooldown) left no rate limit at all —
    the very next miss started another full resync the instant the first one returned.
    Gating on an in-progress flag plus a completion-timestamped cooldown means at most
    one resync is ever in flight, and none pile up right after one finishes either.
    """
    global _resync_in_progress
    if _resync_in_progress:
        return
    if (time.time() - _last_ad_resync_finished) < _AD_RESYNC_COOLDOWN:
        return
    _resync_in_progress = True
    asyncio.create_task(_run_ad_fallback_resync())


async def _run_ad_fallback_resync() -> None:
    """Background body of the fallback resync. Always clears the in-progress flag and
    stamps the completion time on the way out — success, failure, or skipped-locked
    all count as "done" for cooldown purposes."""
    global _resync_in_progress, _last_ad_resync_finished, _ad_cache, _ad_cache_ts

    def _do_resync() -> bool:
        from scripts.sync_ad_users import fetch_ad_users, sync_to_db, _acquire_lock, _release_lock
        # Local file lock — guards against overlapping resyncs from this process.
        # (It does not see the nightly cron, which runs as a separate process on the
        # APP host with its own local lock file; that path is out of scope here.)
        if not _acquire_lock():
            logger.info("ad_fallback_resync_skipped_locked")
            return False
        try:
            users = fetch_ad_users(retries=1)
            sync_to_db(users, dry_run=False)
            return True
        finally:
            _release_lock()

    try:
        ran = await asyncio.to_thread(_do_resync)
        if ran:
            _ad_cache, _ad_cache_ts = [], 0  # force _get_ad_cache() to reload from DB
            logger.info("ad_fallback_resync_completed")
    except Exception as e:
        logger.warning("ad_fallback_resync_failed", error=str(e))
    finally:
        _last_ad_resync_finished = time.time()
        _resync_in_progress = False

# ── /me sliding-refresh debounce ──
# Avoid re-issuing a cookie (and re-querying brand_filter) on every /me call.
# LRU cap prevents unbounded growth if many users hit /me over time.
_me_last_refresh: "OrderedDict[int, float]" = OrderedDict()
_ME_REFRESH_COOLDOWN = 300  # 5 minutes
_ME_REFRESH_LRU_CAP = 1000


# ── Async DB wrappers ──

async def _db_fetch_all(sql: str, params: tuple = ()) -> list[dict]:
    return await asyncio.to_thread(fetch_all, sql, params)


async def _db_fetch_one(sql: str, params: tuple = ()):
    return await asyncio.to_thread(fetch_one, sql, params)


async def _db_execute(sql: str, params: tuple = ()) -> int:
    return await asyncio.to_thread(execute, sql, params)


async def _db_execute_lastid(sql: str, params: tuple = ()) -> int:
    return await asyncio.to_thread(execute_lastid, sql, params)


def _decode_escaped_name(raw: str) -> str:
    r"""가입할 때 `이주원` 같은 이스케이프를 되돌린다.

    ⛔ 가입은 `directory_users.display_name` 을 **복사**한다. 그 순간 깨져 있으면 사본이
       `users` 에 굳고, `directory_users` 가 나중에 고쳐져도 사본은 그대로다 — 로그인
       자동완성은 `COALESCE(u.display_name, ad.display_name)` 이라 깨진 사본이
       이긴다. 그러면 사람은 자기 이름을 못 찾고, 그 행을 클릭하지 못하면
       프론트에서 막혀 **서버에는 기록조차 남지 않는다** (2026-09-01 실제 사고).
    ⚠️ 되돌릴 수 없으면 원본을 그대로 둔다 — 지어내지 않는다.
    """
    text = raw or ""
    if "\\u" not in text:
        return text
    try:
        decoded = text.encode("utf-8").decode("unicode_escape")
    except Exception:
        return text
    if not decoded or "\\u" in decoded:
        return text
    return decoded

# ── Schemas ──

class SignupRequest(BaseModel):
    department: str
    name: str
    password: str
    id: int | None = None  # ad_user_id from search result (preferred)


class SigninRequest(BaseModel):
    department: str
    name: str
    password: str
    id: int | None = None  # ad_user_id from search result (preferred)


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
    return models if models else [ALL_MODELS]


def _user_response(user_row: dict) -> dict:
    """Build UserResponse dict from a joined users+directory_users row."""
    return {
        "id": user_row["id"],
        "email": user_row.get("ad_email") or user_row.get("email") or "",
        "name": user_row.get("ad_name") or user_row.get("display_name") or "",
        "department": user_row.get("department") or "",
        "role": user_row["role"],
        "allowed_models": _resolve_models(user_row["role"], user_row.get("allowed_models")),
    }


def _create_token(user_id: int, email: str = "", brand_filter: str = "", role: str = "user", *,
                  auth_provider: str = "password", entra_oid: str = "", entra_tid: str = "") -> str:
    settings = get_settings()
    payload = {
        "user_id": user_id,
        "email": email,
        "exp": datetime.now(timezone.utc) + timedelta(days=_TOKEN_EXPIRE_DAYS),
        "brand_filter": brand_filter,
        "role": role,
        "purpose": "session",
        "auth_provider": auth_provider,
        "iat": datetime.now(timezone.utc),
    }
    if auth_provider == "entra":
        payload.update(entra_oid=entra_oid, entra_tid=entra_tid)
    return jwt.encode(payload, validate_jwt_secret(settings.jwt_secret_key), algorithm=_ALGORITHM)


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


async def _record_authenticated_visit(user_id: int) -> None:
    """Record one authenticated page open; keep one aggregate row per user/day."""
    try:
        await _db_execute(
            """INSERT INTO user_visits
                   (user_id, visit_date, first_seen_at, last_seen_at, visit_count)
               VALUES (%s, CURDATE(), NOW(), NOW(), 1)
               ON DUPLICATE KEY UPDATE
                   last_seen_at = NOW(),
                   visit_count = visit_count + 1""",
            (user_id,),
        )
    except Exception as e:
        # Analytics must never block sign-in or the main chat experience.
        logger.warning("visit_tracking_failed", user_id=user_id, error=str(e))


# ── Public endpoints (no auth required, for login form) ──

@auth_api_router.get("/methods")
async def auth_methods(request: Request):
    """로그인 화면이 **무엇을 보여줄지 서버에 묻는다.**

    ⚠️ 프론트에 조건을 박으면 설정을 되돌린 날 화면이 따라오지 않는다 —
       Entra 버튼을 서버에 물어보게 한 것과 같은 이유다.
    ⛔ `entra` 판정을 여기서 새로 적지 않는다. `/auth/entra/status` 와 **같은
       함수**를 부른다 (`entra_routes.is_available`) — 두 벌이 되면 갈린다.
    """
    from app.api.entra_routes import is_available as _entra_available

    return {
        "password": password_login_enabled(),
        "entra": _entra_available(request),
    }


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
        FROM directory_users ad
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
        FROM directory_users ad
        LEFT JOIN users u ON ad.id = u.ad_user_id COLLATE utf8mb4_unicode_ci
        WHERE ad.is_active = 1 AND ad.department IS NOT NULL AND ad.department != ''
        ORDER BY COALESCE(u.display_name, ad.display_name), ad.department
    """)
    _ad_cache = rows
    _ad_cache_ts = now
    logger.info("ad_cache_refreshed", count=len(rows))
    return _ad_cache


def _match_cache(cache: list[dict], q: str) -> list[dict]:
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


@auth_api_router.get("/search-name")
async def search_by_name(
    name: str = Query(..., min_length=1, description="Name to search")
):
    """Find AD users by name (searches display_name, ad_name, username).

    Self-heals on a miss: a brand-new hire may not be in directory_users yet if they're
    signing up before tonight's scheduled sync — kick off one live AD resync in the
    background so onboarding never blocks on the cron schedule. This call itself does
    not wait for it (see `_trigger_ad_fallback_resync`); if it lands, the *next*
    keystroke's search picks up the refreshed cache on its own.
    """
    cache = await _get_ad_cache()
    q = name.lower()
    results = _match_cache(cache, q)
    if not results:
        _trigger_ad_fallback_resync()
    return results


# ── Auth endpoints ──

async def _lookup_ad_user(department: str, name: str, ad_user_id: int | None) -> dict | None:
    """Look up an directory_users row by id (preferred) or department+display_name.

    Self-heals on a miss: kicks off a background AD resync so a same-day hire's
    *next* attempt isn't blocked by the nightly-only sync schedule — but does not
    retry inline and does not wait for it. A miss must answer as fast as the DB
    query does, not however long a live AD round-trip takes.
    """
    if ad_user_id:
        ad_user = await _db_fetch_one(
            "SELECT id, display_name, email, department FROM directory_users WHERE is_active = 1 AND id = %s",
            (ad_user_id,),
        )
    else:
        ad_user = await _db_fetch_one(
            "SELECT id, display_name, email, department FROM directory_users "
            "WHERE is_active = 1 AND department = %s AND display_name = %s",
            (department, name),
        )
    if not ad_user:
        _trigger_ad_fallback_resync()
    return ad_user


_WRONG_TEAM_HINT = "그 팀에서 이 이름을 찾지 못했습니다. 다른 팀 소속이 아닌지 확인해 주세요."


async def _not_found_detail(department: str, name: str, ad_user_id: int | None, fallback: str) -> str:
    """Build the error message for a signin/signup lookup miss.

    A miss on the department+name path is very often the '동남아시아팀' vs
    '동남아시아1팀' dropdown mismatch — the name is right, the picked team is wrong.
    A single fast, local, indexed DB lookup (no AD network call) tells us whether the
    name exists under *some other* department; if so, say that instead of the generic
    "not found". Only applies when no id was supplied — an id miss means the account
    itself is gone/inactive, and there's no "other department" to point at.
    """
    if not ad_user_id and await _db_fetch_one(
        "SELECT 1 FROM directory_users WHERE is_active = 1 AND display_name = %s AND department != %s LIMIT 1",
        (name, department),
    ):
        return _WRONG_TEAM_HINT
    return fallback


@auth_api_router.post("/signup")
async def signup(req: SignupRequest, response: Response):
    """Create a new user account linked to an AD user."""
    require_password_login()  # ⛔ DB 조회·해싱보다 먼저
    if len(req.password) < 4:
        raise HTTPException(status_code=400, detail="비밀번호는 4자 이상이어야 합니다")

    ad_user = await _lookup_ad_user(req.department, req.name, req.id)
    if not ad_user:
        detail = await _not_found_detail(
            req.department, req.name, req.id, "해당 부서/이름의 AD 사용자를 찾을 수 없습니다"
        )
        raise HTTPException(status_code=404, detail=detail)

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
        # ⛔ `last_login` 을 비워 두지 마라 — 가입하면 곧바로 로그인 상태가 되므로
        #    다시 `/signin` 을 타지 않는다. 그러면 영영 NULL 로 남아 **출근 브리핑
        #    사전 생성에서 통째로 빠진다** (2026-08-26 이해인 님 제보로 발견).
        "INSERT INTO users "
        "(email, password_hash, display_name, role, allowed_models, ad_user_id, last_login) "
        "VALUES (%s, %s, %s, %s, %s, %s, NOW())",
        (user_email, pw_hash, _decode_escaped_name(ad_user["display_name"]),
         "user", ALL_MODELS, ad_user["id"]),
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
        "allowed_models": [ALL_MODELS],
    }


@auth_api_router.post("/signin")
async def signin(req: SigninRequest, response: Response):
    """Sign in with department + name + password."""
    require_password_login()  # ⛔ DB 조회·bcrypt 검증보다 먼저
    ad_user = await _lookup_ad_user(req.department, req.name, req.id)
    if not ad_user:
        detail = await _not_found_detail(req.department, req.name, req.id, "사용자를 찾을 수 없습니다")
        raise HTTPException(status_code=401, detail=detail)

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


def _survey_prompt_for(user_id: int):
    """지금 물어볼 만족도 설문 임계 (없으면 None)."""
    from app.core.satisfaction import pending_milestone
    return pending_milestone(user_id)


@auth_api_router.get("/me")
async def me(response: Response, user: User = Depends(get_current_user), *, request: Request):
    """Get current authenticated user. Refreshes cookie (sliding session)."""
    # Sliding refresh, debounced: only re-issue cookie if last refresh was > _ME_REFRESH_COOLDOWN ago.
    now = time.time()
    last = _me_last_refresh.get(user.id, 0)
    if now - last > _ME_REFRESH_COOLDOWN:
        bf = await asyncio.to_thread(_lookup_brand_filter, user.id)
        # Authentication proof belongs to this request, never the shared User cache
        # or directory linkage. Refresh cannot turn a legacy login into Entra.
        claims = request.state.session_claims
        fresh_token = _create_token(
            user.id, user.email or "", brand_filter=bf, role=user.role,
            auth_provider=claims.get("auth_provider") or "password",
            entra_oid=claims.get("entra_oid", ""), entra_tid=claims.get("entra_tid", ""),
        )
        _set_cookie(response, fresh_token)
        _me_last_refresh[user.id] = now
        _me_last_refresh.move_to_end(user.id)
        while len(_me_last_refresh) > _ME_REFRESH_LRU_CAP:
            _me_last_refresh.popitem(last=False)

    await _record_authenticated_visit(user.id)

    # 만족도 설문 — 접속일수 10·50·100일차에 한 번 묻는다 (2026-09-02).
    # ⚠️ 방문을 기록하는 바로 이 자리에서 판정한다. 별도 엔드포인트를 두면 왕복이
    #    늘고, 프론트가 그 호출을 빠뜨려도 아무 에러 없이 팝업만 사라진다.
    # ⚠️ 실패해도 로그인·채팅을 막지 않는다 (pending_milestone 이 예외를 삼킨다).
    survey_prompt = await asyncio.to_thread(_survey_prompt_for, user.id)

    can_view_fi = user.role == "admin"
    if not can_view_fi and user.ad_user_id:
        try:
            fi_row = await _db_fetch_one(
                "SELECT can_view_fi FROM directory_users WHERE id = %s",
                (user.ad_user_id,),
            )
            can_view_fi = bool(fi_row and fi_row.get("can_view_fi"))
        except Exception as e:
            logger.warning("fi_permission_lookup_failed", user_id=user.id, error=str(e))

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
        "can_view_fi": can_view_fi,
        "can_view_visitor_analytics": can_view_visitor_analytics(
            user.role, user.can_view_visitor_analytics,
        ),
        "must_change_password": user.must_change_password,
        "requires_group_assignment": user.requires_group_assignment,
        "allowed_models": _resolve_models(user.role, user.allowed_models),
        "brand_filters": brand_filters,
        "my_brand_filter": my_brand_filters[0]["brands"] if my_brand_filters else None,
        # {"milestone": 10, "visit_days": 12} 또는 None
        "survey_prompt": survey_prompt,
    }


class PasswordResetRequestIn(BaseModel):
    department: str = ""
    name: str = ""
    note: str = ""
    id: int | None = None  # ad_user_id (검색 결과에서 오면 이쪽이 정확하다)


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

    # Hash and update. Clears must_change_password — this is the only path off a
    # forced reset, so anything else would leave an admin-issued temporary
    # password standing in permanently.
    new_hash = _bcrypt.hashpw(req.new_password.encode(), _bcrypt.gensalt()).decode()
    await _db_execute(
        "UPDATE users SET password_hash = %s, must_change_password = 0, updated_at = NOW() "
        "WHERE id = %s",
        (new_hash, user.id),
    )
    invalidate_user_cache(user.id)

    logger.info("password_changed", user_id=user.id, name=user.name)
    return {"ok": True}


@auth_api_router.post("/password-reset-request")
async def password_reset_request(req: PasswordResetRequestIn):
    """로그인 못 하는 사람이 **관리자에게 요청만** 남긴다 (로그인 불필요).

    ⛔ **여기서 계정을 건드리지 않는다.** 부서·이름은 로그인 화면이 이미 목록으로
       보여 주므로, 그것만으로 재설정까지 되면 누구나 남의 계정을 초기화할 수 있다.
       셀라에는 재무 손익(FI) 데이터가 있다 — 판단은 사람이 한다
       (2026-08-31 사용자 결정: "관리자 요청 접수만").

    ⚠️ 응답은 **찾았든 못 찾았든 같다.** 여기서 존재 여부를 알려 주면 이 엔드포인트가
       재직자 조회기가 된다. (부서·이름 목록 자체는 로그인 화면이 이미 주지만,
       그것과 "가입해서 계정이 있다" 는 다른 정보다.)
    """
    # ⛔ 쓸 수 없는 비밀번호를 되찾아 주는 접수창은 **덫**이다 — 사람은 기다리고
    #    관리자는 발급하는데 그 비밀번호로는 로그인이 안 된다.
    require_password_login()
    from app.core import password_reset

    same = {"ok": True, "message": "요청이 접수되었습니다. 관리자가 확인 후 연락드립니다."}
    ad_user = await _lookup_ad_user(req.department, req.name, req.id)
    if not ad_user:
        # ⚠️ 조용히 넘기지 말고 로그에는 남긴다 — 이름이 안 맞아 못 찾는 경우가
        #    실제로 있고, 그때 사용자는 접수됐다고 믿고 기다린다. 다만 공개
        #    엔드포인트의 이름·부서·메모는 운영 로그에 남기지 않는다.
        logger.warning("password_reset_request_no_match")
        return same

    result = await asyncio.to_thread(
        password_reset.create, int(ad_user["id"]), req.note or "")
    # ⛔ WARNING 이어야 한다 — 프로덕션은 앱 INFO 를 통째로 버린다 (CLAUDE.md).
    logger.warning("password_reset_requested",
                   ad_user_id=int(ad_user["id"]),
                   duplicate=bool(result.get("duplicate")))
    return same


class GoogleResetCompleteIn(BaseModel):
    new_password: str


@auth_api_router.get("/password-reset/google/start")
async def password_reset_google_start(request: Request):
    """구글 계정으로 **본인이** 확인하고 스스로 비밀번호를 정하는 경로의 시작.

    관리자 요청 경로와 나란히 선다 — 이쪽은 신원을 구글이 서명으로 보증하므로
    계정을 바꿔도 된다. 부서·이름만 아는 사람은 그 보증을 만들 수 없다.
    """
    require_password_login()  # ⛔ 구글까지 보낸 뒤에 막으면 사유를 말할 자리가 없다
    from app.api.auth_routes import _get_redirect_uri
    from app.core import password_reset_google

    redirect_uri = _get_redirect_uri(request)
    # ⛔ 구글에 등록할 수 없는 사내 주소로 보내면 "액세스 차단됨" 으로 끝난다
    #    (2026-09-02 실사용자: `http://ai.cravercorp.internal/...`).
    from app.api.auth_routes import _canonical_browsing_url, _redirect_uri_is_usable
    if not _redirect_uri_is_usable(redirect_uri):
        logger.warning("pwreset_google_unusable_redirect",
                       host=request.headers.get("host", ""), redirect_uri=redirect_uri)
        canonical = _canonical_browsing_url()
        raise HTTPException(
            status_code=400,
            detail=(f"이 주소에서는 구글 확인을 할 수 없습니다. {canonical} 로 접속해 "
                    f"다시 시도하거나, 관리자에게 재설정을 요청해 주세요."
                    if canonical else
                    "이 주소에서는 구글 확인을 할 수 없습니다. 관리자에게 요청해 주세요."),
        )
    try:
        state = await asyncio.to_thread(password_reset_google.issue_state)
        url = password_reset_google.build_auth_url(redirect_uri, state)
    except password_reset_google.ResetUnavailable as unavailable:
        raise HTTPException(status_code=503, detail=unavailable.body)

    # ⛔ 여기서 구글로 넘어간 뒤 막히면 **우리 쪽에는 아무 흔적도 안 남는다** —
    #    사용자가 눌렀는지조차 알 수 없어 원인을 추측하게 된다 (2026-09-01 실제로
    #    그렇게 두 번 헛짚었다). 어느 호스트로 접속해 어떤 리다이렉트 주소가
    #    나갔는지를 남긴다. 개인 정보는 없다 — 호스트와 리다이렉트 주소뿐이다.
    # ⚠️ INFO 는 프로덕션에서 통째로 버려진다 (CLAUDE.md) — WARNING 이어야 남는다.
    logger.warning(
        "pwreset_google_start",
        browsing_host=request.headers.get("host", ""),
        redirect_uri=redirect_uri,
    )
    return RedirectResponse(url=url, status_code=302)


@auth_api_router.get("/password-reset/google/land")
async def password_reset_google_land(request: Request, rc: str = Query("")):
    """확인 증표를 **주소창에서 쿠키로 옮긴다.**

    ⚠️ 증표가 URL 에 남으면 방문기록·리퍼러·화면 공유에 그대로 실린다. 여기서
       한 번 받아 HttpOnly 쿠키로 옮기고 깨끗한 주소로 되돌린다 (증표 자체는
       일회용이고 5분이지만, 남길 이유가 없는 것은 남기지 않는다).
    """
    require_password_login()  # ⛔ 증표를 쿠키로 옮겨 봐야 완료가 403 이다
    from app.core import password_reset_google

    if await asyncio.to_thread(password_reset_google.peek_grant, rc) is None:
        return RedirectResponse(url="/login?reset=expired", status_code=303)

    response = RedirectResponse(url="/login?reset=1", status_code=303)
    response.set_cookie(
        key=password_reset_google.GRANT_COOKIE,
        value=rc,
        max_age=int(password_reset_google.GRANT_TTL.total_seconds()),
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
        path=password_reset_google.GRANT_COOKIE_PATH,
    )
    return response


@auth_api_router.post("/password-reset/google/complete")
async def password_reset_google_complete(
    req: GoogleResetCompleteIn, request: Request, response: Response
):
    """구글로 확인된 사람이 새 비밀번호를 정한다."""
    # ⛔ 증표를 태우기 전에 막는다 — 뒤에 두면 일회용 증표만 소모되고 끝난다.
    require_password_login()
    from app.core import password_reset, password_reset_google

    # ⛔ 길이 검사를 **증표를 태우기 전에** 한다. 뒤에 두면 짧게 한 번 눌렀다가
    #    증표가 소모돼 처음부터 다시 해야 한다.
    if len(req.new_password) < password_reset_google.MIN_PASSWORD_LEN:
        raise HTTPException(
            status_code=400,
            detail=f"새 비밀번호는 {password_reset_google.MIN_PASSWORD_LEN}자 이상이어야 합니다",
        )

    code = request.cookies.get(password_reset_google.GRANT_COOKIE, "")
    try:
        user_id = await asyncio.to_thread(password_reset_google.consume_grant, code)
    except Exception:
        raise HTTPException(
            status_code=400,
            detail="확인이 만료되었습니다. 로그인 화면에서 다시 시도해 주세요.",
        )

    await asyncio.to_thread(password_reset_google.set_password, user_id, req.new_password)
    invalidate_user_cache(user_id)

    # ⛔ 대기 중이던 관리자 요청을 함께 닫는다 — 본인이 이미 해결했는데 대기열에
    #    남아 있으면 관리자가 쓸데없이 임시 비밀번호를 발급하고, 그 순간 방금
    #    정한 비밀번호가 무효가 된다 ("수정과 표시는 한 작업이다").
    row = await _db_fetch_one("SELECT ad_user_id FROM users WHERE id = %s", (user_id,))
    if row and row.get("ad_user_id"):
        await asyncio.to_thread(password_reset.close_for_ad_user, int(row["ad_user_id"]))

    response.delete_cookie(
        key=password_reset_google.GRANT_COOKIE,
        path=password_reset_google.GRANT_COOKIE_PATH,
    )
    logger.warning("pwreset_google_completed", user_id=user_id)
    return {"ok": True, "message": "비밀번호를 변경했습니다. 새 비밀번호로 로그인해 주세요."}


@auth_api_router.post("/logout")
async def logout(response: Response):
    """Clear auth cookie."""
    response.delete_cookie(key="token", path="/")
    return {"ok": True}


# ── CRM OAuth 콜백 프록시 ────────────────────────────────────────
# Google이 track.skin1004.app/api/auth/google/callback 으로 리디렉트하면
# 이 서버(포트 3000)에서 받아 CRM(포트 3100)으로 전달한다.
_CRM_BASE = "http://172.16.1.250:3100"

@auth_api_router.get("/google/callback")
async def crm_google_callback_proxy(request: Request):
    qs = request.url.query
    target = f"{_CRM_BASE}/api/auth/google/callback"
    if qs:
        target += f"?{qs}"
    return RedirectResponse(url=target, status_code=302)
