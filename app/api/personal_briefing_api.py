"""Authenticated, cached-first API for the personal login briefing."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException

from app.api.auth_middleware import get_current_user
from app.config import get_settings
from app.core.personal_briefing import get_cached_for_user, refresh_for_user
from app.db.models import User


router = APIRouter(prefix="/api/personal-briefing", tags=["personal-briefing"])


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
    try:
        return await asyncio.wait_for(refresh_for_user(user), timeout=15.0)
    except asyncio.TimeoutError:
        cached = await asyncio.to_thread(get_cached_for_user, user)
        for key in ("calendar", "mail"):
            section = cached.get(key, {})
            if section.get("status") in {"empty", "ready"} and not section.get("items"):
                section["status"] = "error"
                section["error_code"] = "google_timeout"
        cached["needs_refresh"] = False
        return cached
