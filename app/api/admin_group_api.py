"""Admin endpoints: AD user & group management (MariaDB)."""

import asyncio
import subprocess
import sys
from typing import Optional

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.api.auth_middleware import get_current_user
from app.db.models import User
from app.db.mariadb import fetch_all, fetch_one, execute, execute_lastid

logger = structlog.get_logger(__name__)

group_router = APIRouter(prefix="/api/admin/groups", tags=["admin-groups"])
ad_router = APIRouter(prefix="/api/admin/ad", tags=["admin-ad"])


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


# ── Group CRUD ──

@group_router.get("")
async def list_groups(user: User = Depends(get_current_user)):
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
    logger.info("group_deleted", name=group["name"], by=admin.email)
    return {"ok": True}


# ── Group Membership ──

@group_router.get("/{group_id}/members")
async def list_group_members(group_id: int, user: User = Depends(get_current_user)):
    """List members of a group."""
    members = await _fetch_all("""
        SELECT a.id, a.username, a.display_name, a.email, a.department
        FROM ad_users a
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
                "SELECT id FROM ad_users WHERE is_active = 1 AND department LIKE %s",
                (f"{req.department}%",),
            )
        else:
            dept_users = await _fetch_all(
                "SELECT id FROM ad_users WHERE is_active = 1 AND department = %s",
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
    for uid in new_ids:
        await _execute(
            "INSERT INTO user_groups (ad_user_id, group_id) VALUES (%s, %s)",
            (uid, group_id),
        )
        added += 1

    logger.info("users_assigned", group=group["name"], added=added, skipped=skipped,
                dept=req.department, by=admin.email)
    return {"ok": True, "added": added, "skipped": skipped, "total": len(user_ids)}


@group_router.delete("/{group_id}/members")
async def remove_users_from_group(
    group_id: int, req: RemoveUsers, admin: User = Depends(_require_admin)
):
    """Remove AD users from a group."""
    removed = 0
    for uid in req.ad_user_ids:
        r = await _execute(
            "DELETE FROM user_groups WHERE ad_user_id = %s AND group_id = %s",
            (uid, group_id),
        )
        removed += r

    logger.info("users_removed", group_id=group_id, removed=removed, by=admin.email)
    return {"ok": True, "removed": removed}


# ── AD Users ──

@ad_router.get("/users")
async def list_ad_users(
    user: User = Depends(get_current_user),
    dept: Optional[str] = Query(None, description="Filter by department keyword"),
    search: Optional[str] = Query(None, description="Search name/email"),
    group_id: Optional[int] = Query(None, description="Filter by group"),
    unassigned: bool = Query(False, description="Only unassigned users"),
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

    where = " AND ".join(conditions)

    sql = f"""
        SELECT a.id, a.username, a.display_name, a.email, a.department,
               GROUP_CONCAT(g.name SEPARATOR ', ') as group_names
        FROM ad_users a
        LEFT JOIN user_groups ug ON a.id = ug.ad_user_id
        LEFT JOIN access_groups g ON ug.group_id = g.id
        WHERE {where}
        GROUP BY a.id
        ORDER BY a.department, a.display_name
    """
    users = await _fetch_all(sql, tuple(params))
    return users


@ad_router.get("/departments")
async def list_departments(user: User = Depends(get_current_user)):
    """List all departments with user counts."""
    depts = await _fetch_all("""
        SELECT department, COUNT(*) as cnt
        FROM ad_users
        WHERE is_active = 1
        GROUP BY department
        ORDER BY department
    """)
    return depts


@ad_router.post("/sync")
async def sync_ad_users(admin: User = Depends(_require_admin)):
    """Trigger AD user sync (runs sync_ad_users.py)."""
    import os
    script = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "scripts", "sync_ad_users.py",
    )
    try:
        proc = await asyncio.to_thread(
            subprocess.run,
            [sys.executable, "-X", "utf8", script],
            capture_output=True, text=True, timeout=60,
        )
        logger.info("ad_sync_triggered", by=admin.email, returncode=proc.returncode)
        return {
            "ok": proc.returncode == 0,
            "output": proc.stdout[-500:] if proc.stdout else "",
            "error": proc.stderr[-300:] if proc.stderr else "",
        }
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="AD sync timed out")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@ad_router.get("/stats")
async def ad_stats(user: User = Depends(get_current_user)):
    """Quick stats for admin dashboard (single query)."""
    row = await _fetch_one("""
        SELECT
            (SELECT COUNT(*) FROM ad_users WHERE is_active = 1) as total_ad_users,
            (SELECT COUNT(DISTINCT ad_user_id) FROM user_groups) as assigned_users,
            (SELECT COUNT(*) FROM access_groups) as total_groups
    """)
    return {
        "total_ad_users": row["total_ad_users"],
        "assigned_users": row["assigned_users"],
        "unassigned_users": row["total_ad_users"] - row["assigned_users"],
        "total_groups": row["total_groups"],
    }
