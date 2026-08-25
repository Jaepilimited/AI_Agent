"""Authenticated, cached-first API for the personal login briefing."""

from __future__ import annotations

import asyncio
import copy
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException

from app.api.auth_middleware import get_current_user
from app.config import get_settings
from app.core.personal_briefing import SEOUL, get_cached_for_user, refresh_for_user
from app.db.models import User


router = APIRouter(prefix="/api/personal-briefing", tags=["personal-briefing"])
_REFRESH_BUDGET_SECONDS = 15.0
_ACTIVE_REFRESH_TASKS: set[asyncio.Task] = set()


def _remaining_seconds(deadline: float) -> float:
    """Return the remaining request budget from the active event loop clock."""

    return max(0.0, deadline - asyncio.get_running_loop().time())


def _tracked_refresh(user: User, now: datetime) -> asyncio.Task:
    """Keep timed-out refreshes alive so their user lock still guards revoke."""

    task = asyncio.create_task(refresh_for_user(user, now=now))
    _ACTIVE_REFRESH_TASKS.add(task)

    def finish(completed: asyncio.Task) -> None:
        _ACTIVE_REFRESH_TASKS.discard(completed)
        if not completed.cancelled():
            completed.exception()

    task.add_done_callback(finish)
    return task


def _minimal_timeout_response(now: datetime) -> dict:
    """Return a display-safe envelope when even the cache cannot be read in time."""

    return {
        "enabled": True,
        "for_date": str(now.astimezone(SEOUL).date()),
        "timezone": "Asia/Seoul",
        "generated_at": "",
        "needs_refresh": False,
        "google": {"connected": False, "account": ""},
        "priorities": [],
        "calendar": {"status": "error", "items": [], "truncated": False, "error_code": "google_timeout"},
        "mail": {
            "status": "error", "count_label": "0건", "unread": 0, "summary": "",
            "action_candidates": [], "items": [], "truncated": False, "error_code": "google_timeout",
        },
        "business": {"status": "error", "item": None},
    }


def _timeout_response_from_cache(cached: dict | None, now: datetime) -> dict:
    """Keep a same-day last-known-good payload when refresh exhausts its budget."""

    day = str(now.astimezone(SEOUL).date())
    if not cached or cached.get("for_date") != day:
        return _minimal_timeout_response(now)
    result = copy.deepcopy(cached)
    for key in ("calendar", "mail"):
        section = result.get(key, {})
        if section.get("items"):
            section["status"] = "stale"
            section["error_code"] = "google_timeout"
        elif section.get("status") in {"empty", "ready", "stale", "error"}:
            section["status"] = "error"
            section["error_code"] = "google_timeout"
    result["needs_refresh"] = False
    return result


@router.get("")
async def get_personal_briefing(user: User = Depends(get_current_user)) -> dict:
    """Return only the JWT owner's cached briefing without Google API calls."""

    if not get_settings().personal_briefing_enabled:
        return {"enabled": False}
    return await asyncio.to_thread(get_cached_for_user, user)


@router.post("/refresh")
async def refresh_personal_briefing(user: User = Depends(get_current_user)) -> dict:
    """Refresh the JWT owner's briefing within the whole-request budget."""

    if not get_settings().personal_briefing_enabled:
        raise HTTPException(status_code=404, detail="Personal briefing disabled")

    now = datetime.now(SEOUL)
    deadline = asyncio.get_running_loop().time() + _REFRESH_BUDGET_SECONDS
    try:
        cached = await asyncio.to_thread(get_cached_for_user, user, now)
    except Exception:
        cached = None
    refresh_task = _tracked_refresh(user, now)
    try:
        return await asyncio.wait_for(
            asyncio.shield(refresh_task), timeout=_remaining_seconds(deadline),
        )
    except asyncio.TimeoutError:
        return _timeout_response_from_cache(cached, now)
