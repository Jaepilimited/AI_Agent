"""Authenticated, cached-first API for the personal login briefing."""

from __future__ import annotations

import asyncio
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException

from app.api.auth_middleware import get_current_user
from app.config import get_settings
from app.core.personal_briefing import SEOUL, get_cached_for_user, refresh_for_user
from app.db.models import User


router = APIRouter(prefix="/api/personal-briefing", tags=["personal-briefing"])
_REFRESH_BUDGET_SECONDS = 15.0


def _remaining_seconds(deadline: float) -> float:
    """Return the remaining request budget from the active event loop clock."""

    return max(0.0, deadline - asyncio.get_running_loop().time())


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
        return await asyncio.wait_for(
            refresh_for_user(user, now=now), timeout=_remaining_seconds(deadline),
        )
    except asyncio.TimeoutError:
        remaining = _remaining_seconds(deadline)
        if remaining <= 0:
            return _minimal_timeout_response(now)
        try:
            cached = await asyncio.wait_for(
                asyncio.to_thread(get_cached_for_user, user, now), timeout=remaining,
            )
        except asyncio.TimeoutError:
            return _minimal_timeout_response(now)
        for key in ("calendar", "mail"):
            section = cached.get(key, {})
            if section.get("status") in {"empty", "ready"} and not section.get("items"):
                section["status"] = "error"
                section["error_code"] = "google_timeout"
        cached["needs_refresh"] = False
        return cached
