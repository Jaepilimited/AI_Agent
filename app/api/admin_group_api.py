"""Admin endpoints: Cella employee directory and group management."""

import asyncio
import secrets
import string
from typing import Optional

import bcrypt as _bcrypt
import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.api.auth_middleware import get_current_user, invalidate_user_cache
from app.db.models import User
from app.db.mariadb import fetch_all, fetch_one, execute, execute_lastid

logger = structlog.get_logger(__name__)

group_router = APIRouter(prefix="/api/admin/groups", tags=["admin-groups"])
ad_router = APIRouter(prefix="/api/admin/directory", tags=["admin-directory"])


def _require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


# ── Async DB wrappers (avoid blocking event loop) ──

async def _fetch_all(sql: str, params: tuple = ()) -> list[dict]:
    return await asyncio.to_thread(fetch_all, sql, params)

async def _fetch_one(sql: str, params: tuple = ()):
    return await asyncio.to_thread(fetch_one, sql, params)

async def _execute(sql: str, params: tuple = ()) -> int:
    return await asyncio.to_thread(execute, sql, params)

async def _execute_lastid(sql: str, params: tuple = ()) -> int:
    return await asyncio.to_thread(execute_lastid, sql, params)


# ── Schemas ──

class GroupCreate(BaseModel):
    name: str
    description: str = ""
    brand_filter: Optional[str] = None


class GroupUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    brand_filter: Optional[str] = None


class AssignUsers(BaseModel):
    ad_user_ids: list[int] = []
    department: Optional[str] = None
    include_sub: bool = True


class RemoveUsers(BaseModel):
    ad_user_ids: list[int]


class FiAccessUpdate(BaseModel):
    can_view_fi: bool


class VisitorAnalyticsAccessUpdate(BaseModel):
    can_view_visitor_analytics: bool


# ── Group CRUD ──

@group_router.get("")
async def list_groups(user: User = Depends(_require_admin)):
    """List all groups with member counts."""
    groups = await _fetch_all("""
        SELECT g.id, g.name, g.description, g.brand_filter, g.created_at,
               COUNT(ug.id) as member_count
        FROM access_groups g
        LEFT JOIN user_groups ug ON g.id = ug.group_id
        GROUP BY g.id
        ORDER BY g.name
    """)
    return groups


@group_router.post("")
async def create_group(req: GroupCreate, admin: User = Depends(_require_admin)):
    """Create a new group."""
    existing = await _fetch_one("SELECT id FROM access_groups WHERE name = %s", (req.name,))
    if existing:
        raise HTTPException(status_code=400, detail="Group name already exists")

    gid = await _execute_lastid(
        "INSERT INTO access_groups (name, description, brand_filter) VALUES (%s, %s, %s)",
        (req.name, req.description, req.brand_filter or None),
    )
    logger.info("group_created", name=req.name, by=admin.email)
    return {"ok": True, "id": gid, "name": req.name}


@group_router.put("/{group_id}")
async def update_group(group_id: int, req: GroupUpdate, admin: User = Depends(_require_admin)):
    """Update group name/description."""
    group = await _fetch_one("SELECT id FROM access_groups WHERE id = %s", (group_id,))
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")

    if req.name:
        dup = await _fetch_one(
            "SELECT id FROM access_groups WHERE name = %s AND id != %s",
            (req.name, group_id),
        )
        if dup:
            raise HTTPException(status_code=400, detail="Group name already exists")
        await _execute("UPDATE access_groups SET name = %s WHERE id = %s", (req.name, group_id))

    if req.description is not None:
        await _execute("UPDATE access_groups SET description = %s WHERE id = %s", (req.description, group_id))

    if req.brand_filter is not None:
        # Empty string → NULL (clear filter)
        bf_val = req.brand_filter.strip() if req.brand_filter.strip() else None
        await _execute("UPDATE access_groups SET brand_filter = %s WHERE id = %s", (bf_val, group_id))
        invalidate_user_cache()

    logger.info("group_updated", group_id=group_id, by=admin.email)
    return {"ok": True}


@group_router.delete("/{group_id}")
async def delete_group(group_id: int, admin: User = Depends(_require_admin)):
    """Delete a group (members are unassigned, not deleted)."""
    group = await _fetch_one("SELECT id, name FROM access_groups WHERE id = %s", (group_id,))
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")

    await _execute("DELETE FROM user_groups WHERE group_id = %s", (group_id,))
    await _execute("DELETE FROM access_groups WHERE id = %s", (group_id,))
    invalidate_user_cache()
    logger.info("group_deleted", name=group["name"], by=admin.email)
    return {"ok": True}


# ── Group Membership ──

@group_router.get("/{group_id}/members")
async def list_group_members(group_id: int, user: User = Depends(_require_admin)):
    """List members of a group."""
    members = await _fetch_all("""
        SELECT a.id, a.username, a.display_name, a.email, a.department
        FROM directory_users a
        JOIN user_groups ug ON a.id = ug.ad_user_id
        WHERE ug.group_id = %s
        ORDER BY a.display_name
    """, (group_id,))
    return members


@group_router.post("/{group_id}/members")
async def assign_users_to_group(
    group_id: int, req: AssignUsers, admin: User = Depends(_require_admin)
):
    """Assign AD users to a group. Supports individual IDs or bulk by department."""
    group = await _fetch_one("SELECT id, name FROM access_groups WHERE id = %s", (group_id,))
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")

    # Resolve user IDs — from explicit list or department lookup
    user_ids = list(req.ad_user_ids)

    if req.department:
        if req.include_sub:
            dept_users = await _fetch_all(
                "SELECT id FROM directory_users WHERE is_active = 1 AND department LIKE %s",
                (f"{req.department}%",),
            )
        else:
            dept_users = await _fetch_all(
                "SELECT id FROM directory_users WHERE is_active = 1 AND department = %s",
                (req.department,),
            )
        user_ids.extend(u["id"] for u in dept_users)

    if not user_ids:
        raise HTTPException(status_code=400, detail="배정할 사용자가 없습니다")

    # Batch: find already-assigned users in one query
    placeholders = ",".join(["%s"] * len(user_ids))
    existing_rows = await _fetch_all(
        f"SELECT ad_user_id FROM user_groups WHERE group_id = %s AND ad_user_id IN ({placeholders})",
        (group_id, *user_ids),
    )
    existing_ids = {r["ad_user_id"] for r in existing_rows}
    new_ids = [uid for uid in user_ids if uid not in existing_ids]
    skipped = len(existing_ids)

    # Batch insert new assignments
    added = 0
    if new_ids:
        values_sql = ",".join(["(%s, %s)"] * len(new_ids))
        params = tuple(p for uid in new_ids for p in (uid, group_id))
        await _execute(
            f"INSERT INTO user_groups (ad_user_id, group_id) VALUES {values_sql}",
            params,
        )
        added = len(new_ids)
        invalidate_user_cache()

    logger.info("users_assigned", group=group["name"], added=added, skipped=skipped,
                dept=req.department, by=admin.email)
    return {"ok": True, "added": added, "skipped": skipped, "total": len(user_ids)}


@group_router.delete("/{group_id}/members")
async def remove_users_from_group(
    group_id: int, req: RemoveUsers, admin: User = Depends(_require_admin)
):
    """Remove AD users from a group."""
    removed = 0
    if req.ad_user_ids:
        placeholders = ",".join(["%s"] * len(req.ad_user_ids))
        removed = await _execute(
            f"DELETE FROM user_groups WHERE group_id = %s AND ad_user_id IN ({placeholders})",
            (group_id, *req.ad_user_ids),
        )
        invalidate_user_cache()

    logger.info("users_removed", group_id=group_id, removed=removed, by=admin.email)
    return {"ok": True, "removed": removed}


# ── AD Users ──

@ad_router.get("/users")
async def list_ad_users(
    user: User = Depends(_require_admin),
    dept: Optional[str] = Query(None, description="Filter by department keyword"),
    search: Optional[str] = Query(None, description="Search name/email"),
    group_id: Optional[int] = Query(None, description="Filter by group"),
    unassigned: bool = Query(False, description="Only unassigned users"),
    fi_only: bool = Query(False, description="Only FI-enabled users"),
    visitor_only: bool = Query(False, description="Only visitor analytics-enabled users"),
):
    """List AD users with optional filters."""
    conditions = ["a.is_active = 1"]
    params = []

    if dept:
        conditions.append("a.department LIKE %s")
        params.append(f"%{dept}%")

    if search:
        conditions.append("(a.display_name LIKE %s OR a.email LIKE %s OR a.username LIKE %s)")
        params.extend([f"%{search}%"] * 3)

    if group_id:
        conditions.append("ug.group_id = %s")
        params.append(group_id)

    if unassigned:
        conditions.append("ug.group_id IS NULL")

    if fi_only:
        conditions.append("a.can_view_fi = 1")

    if visitor_only:
        conditions.append("a.can_view_visitor_analytics = 1")

    where = " AND ".join(conditions)

    sql = f"""
        SELECT a.id, a.username, a.display_name, a.email, a.department,
               a.can_view_fi, a.can_view_visitor_analytics, u.id AS user_id,
               (a.entra_oid IS NOT NULL) AS entra_linked, a.identity_source, a.last_signin_at,
               GROUP_CONCAT(g.name SEPARATOR ', ') as group_names
        FROM directory_users a
        LEFT JOIN users u ON u.ad_user_id = a.id
        LEFT JOIN user_groups ug ON a.id = ug.ad_user_id
        LEFT JOIN access_groups g ON ug.group_id = g.id
        WHERE {where}
        GROUP BY a.id
        ORDER BY a.department, a.display_name
    """
    users = await _fetch_all(sql, tuple(params))
    for row in users:
        row["entra_linked"] = bool(row.get("entra_linked"))
    return users


@ad_router.put("/users/{ad_user_id}/fi")
async def update_fi_access(
    ad_user_id: int,
    req: FiAccessUpdate,
    admin: User = Depends(_require_admin),
):
    """Grant or revoke FI_LLM_Flat access for one AD user."""
    target = await _fetch_one(
        "SELECT id, username, display_name FROM directory_users WHERE id = %s",
        (ad_user_id,),
    )
    if not target:
        raise HTTPException(status_code=404, detail="AD user not found")

    await _execute(
        "UPDATE directory_users SET can_view_fi = %s WHERE id = %s",
        (1 if req.can_view_fi else 0, ad_user_id),
    )
    logger.info("admin_update_fi", target=target["username"], by=admin.email)
    return {
        "ok": True,
        "ad_user_id": ad_user_id,
        "can_view_fi": req.can_view_fi,
    }


@ad_router.put("/users/{ad_user_id}/visitor-analytics")
async def update_visitor_analytics_access(
    ad_user_id: int,
    req: VisitorAnalyticsAccessUpdate,
    admin: User = Depends(_require_admin),
):
    """Grant or revoke the visitor analytics tab for one AD user."""
    target = await _fetch_one(
        "SELECT a.id, a.username, a.display_name, u.id AS user_id "
        "FROM directory_users a LEFT JOIN users u ON u.ad_user_id = a.id "
        "WHERE a.id = %s",
        (ad_user_id,),
    )
    if not target:
        raise HTTPException(status_code=404, detail="AD user not found")

    await _execute(
        "UPDATE directory_users SET can_view_visitor_analytics = %s WHERE id = %s",
        (1 if req.can_view_visitor_analytics else 0, ad_user_id),
    )
    if target.get("user_id"):
        invalidate_user_cache(target["user_id"])
    logger.info(
        "admin_update_visitor_analytics_access",
        target=target["username"],
        allowed=req.can_view_visitor_analytics,
        by=admin.email,
    )
    return {
        "ok": True,
        "ad_user_id": ad_user_id,
        "can_view_visitor_analytics": req.can_view_visitor_analytics,
    }


_TEMP_PASSWORD_LENGTH = 14
# 스크린샷·화면 낭독으로 옮겨 적을 사람을 위해 헷갈리는 글자(0/O, 1/l/I)는 뺀다.
_TEMP_PASSWORD_ALPHABET = "".join(
    c for c in (string.ascii_letters + string.digits) if c not in "0O1lI"
)


def _generate_temp_password() -> str:
    """관리자가 고르지 않는다 — 재사용·추측을 막기 위해 서버가 `secrets` 로 생성한다."""
    return "".join(secrets.choice(_TEMP_PASSWORD_ALPHABET) for _ in range(_TEMP_PASSWORD_LENGTH))


@ad_router.post("/users/{ad_user_id}/reset-password")
async def reset_password(
    ad_user_id: int,
    admin: User = Depends(_require_admin),
):
    """관리자가 로컬 로그인 비밀번호를 초기화한다 — 잊어버린 사람의 유일한 복귀 경로.

    비밀번호는 `users` 테이블에 있다 (`can_view_fi` 는 `directory_users` — 혼동 금지).
    평문은 이 응답에 딱 한 번 실리고, DB·로그 어디에도 남지 않는다.
    `must_change_password` 를 세워 두면, 로그인은 되지만 본인이 새 비밀번호를
    정할 때까지(`/api/auth/change-password`) 그 외 어떤 요청도 `get_current_user`
    관문에서 막힌다 — 그 강제가 없으면 이 임시 비밀번호가 영구 비밀번호가 된다.

    ⛔ 로컬 ID/PW 가 꺼져 있으면 **발급 자체를 막는다** (2026-09-08). 여기서
       발급하면 두 가지가 한꺼번에 나쁘다: ① 관리자가 건넨 비밀번호로는
       로그인이 403 이고 ② `must_change_password` 가 서서 그 사람은 회사
       계정으로 들어와도 **모든 요청이 막힌다**. 못 쓰는 열쇠를 주면서 문까지
       잠그는 셈이다.
    """
    from app.api.auth_api import require_password_login
    require_password_login()

    target = await _fetch_one(
        "SELECT u.id AS user_id, u.display_name, a.username "
        "FROM users u JOIN directory_users a ON u.ad_user_id = a.id "
        "WHERE a.id = %s",
        (ad_user_id,),
    )
    if not target:
        raise HTTPException(
            status_code=404,
            detail="가입된 사용자를 찾을 수 없습니다 (AD 사용자만으로는 초기화할 수 없습니다)",
        )

    temp_password = _generate_temp_password()
    pw_hash = _bcrypt.hashpw(temp_password.encode(), _bcrypt.gensalt()).decode()

    await _execute(
        "UPDATE users SET password_hash = %s, must_change_password = 1, updated_at = NOW() "
        "WHERE id = %s",
        (pw_hash, target["user_id"]),
    )
    invalidate_user_cache(target["user_id"])

    # ⛔ 평문은 여기 로그에도 남기지 않는다 — 관리자가 화면에서 한 번 읽고 전달한다.
    # INFO 는 프로덕션에서 버려지므로(CLAUDE.md) 실행 기록이 남게 WARNING 으로 남긴다.
    logger.warning(
        "admin_password_reset",
        target_username=target["username"],
        target_user_id=target["user_id"],
        target_ad_user_id=ad_user_id,
        by=admin.email,
    )
    # ⛔ **초기화와 요청 닫기는 한 동작이다.** 따로 두면 관리자가 초기화만 하고
    #    요청은 계속 대기로 남아 다음에 볼 때도 맨 앞에 뜬다 — 붐따가 정확히
    #    그렇게 36건 쌓였다 (CLAUDE.md 붐따 규칙: "수정과 표시는 한 작업이다").
    from app.core import password_reset
    await asyncio.to_thread(password_reset.close_for_ad_user, ad_user_id, admin.id)
    return {
        "ok": True,
        "ad_user_id": ad_user_id,
        "temporary_password": temp_password,
    }


@ad_router.get("/password-reset-requests")
async def list_password_reset_requests(admin: User = Depends(_require_admin)):
    """대기 중인 비밀번호 재설정 요청 — 로그인 화면에서 본인이 남긴 것.

    ⚠️ `registered` 가 거짓이면 **초기화가 아니라 가입 안내**를 해야 한다
       (AD 에는 있지만 셀라 가입 전이라 초기화할 대상이 없다).
    """
    from app.core import password_reset
    rows = await asyncio.to_thread(password_reset.open_requests)
    return {"requests": [{
        "id": r["id"],
        "ad_user_id": r["ad_user_id"],
        "name": r.get("display_name"),
        "department": r.get("department"),
        "note": r.get("note") or "",
        "registered": bool(r.get("registered")),
        "created_at": str(r.get("created_at") or ""),
    } for r in rows]}


@ad_router.get("/departments")
async def list_departments(user: User = Depends(_require_admin)):
    """List all departments with user counts."""
    depts = await _fetch_all("""
        SELECT department, COUNT(*) as cnt
        FROM directory_users
        WHERE is_active = 1
        GROUP BY department
        ORDER BY department
    """)
    return depts


@ad_router.post("/sync")
async def sync_ad_users(admin: User = Depends(_require_admin)):
    """Refresh employee profiles from Entra without changing Cella grants."""
    from app.core.user_directory import sync_directory
    return await asyncio.to_thread(sync_directory)


@ad_router.get("/stats")
async def ad_stats(user: User = Depends(_require_admin)):
    """Quick stats for admin dashboard (single query)."""
    row = await _fetch_one("""
        SELECT
            (SELECT COUNT(*) FROM directory_users WHERE is_active = 1) as total_ad_users,
            (SELECT COUNT(DISTINCT ad_user_id) FROM user_groups) as assigned_users,
            (SELECT COUNT(*) FROM access_groups) as total_groups,
            (SELECT COUNT(*) FROM directory_users WHERE is_active = 1 AND can_view_fi = 1) as fi_allowed_users
    """)
    return {
        "total_ad_users": row["total_ad_users"],
        "assigned_users": row["assigned_users"],
        "unassigned_users": row["total_ad_users"] - row["assigned_users"],
        "total_groups": row["total_groups"],
        "fi_allowed_users": row["fi_allowed_users"],
    }
