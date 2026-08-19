# -*- coding: utf-8 -*-
"""알림 API — 지금은 '나에게 공유된 보고서' 하나뿐이다.

메일로 보내고 싶었지만 **서버에서 메일이 나가지 않는다** (2026-08-19 실측: WAS·APP 양쪽
모두 SMTP 25/587 차단, 로컬 MTA 없음, Google OAuth 스코프는 gmail.readonly).
IT 가 릴레이를 열어주기 전까지는 앱 안에서 알린다. 열리면 **같은 문구를** 메일로도
보내도록 확장한다 — 이 API 의 모양은 그대로 둔다.

⚠️ 대상 판정은 **서버에서 JWT 의 user_id 로만** 한다. 프론트가 보낸 값을 믿지 않는
기존 원칙과 같다 — 남의 알림을 보게 되면 보고서 제목·질문이 그대로 새어 나간다.
"""
from __future__ import annotations

import asyncio

import structlog
from fastapi import APIRouter, Depends

from app.api.auth_middleware import get_current_user
from app.db.models import User
from app.reports import store

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/notifications", tags=["notifications"])


@router.get("")
async def list_notifications(user: User = Depends(get_current_user)) -> dict:
    """내 알림 목록 + 안 읽은 수.

    `type` 을 달아 돌려준다 — 나중에 붐따 회신·자가 점검 알림을 같은 목록에 얹을 때
    프론트를 다시 고치지 않기 위해서다 (지금 만들지는 않는다).
    """
    rows = await asyncio.to_thread(store.list_notifications, user.id)
    items = [{
        "type": "report_share",
        "report_id": r["report_id"],
        "title": r.get("title") or r.get("question") or "보고서",
        "from_name": r.get("from_name") or "",
        "created_at": r["created_at"].isoformat() if r.get("created_at") else "",
        "seen": bool(r.get("seen_at")),
        "url": f"/api/reports/{r['report_id']}",
    } for r in rows]
    return {"unseen": sum(1 for i in items if not i["seen"]), "items": items}


@router.post("/seen")
async def mark_all_seen(user: User = Depends(get_current_user)) -> dict:
    n = await asyncio.to_thread(store.mark_all_seen, user.id)
    logger.info("notifications_marked_seen", user_id=user.id, count=n)
    return {"marked": n}
