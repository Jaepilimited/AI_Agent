# -*- coding: utf-8 -*-
"""알림 API — 공유받은 보고서 · 내 붐따 처리 상태 · 업데이트 공지.

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
from app.core import announcements, briefing, feedback_inbox
from app.reports import store

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/notifications", tags=["notifications"])


def _neg(iso: str) -> str:
    """문자열 시각을 내림차순으로 정렬하기 위한 키 (최신이 앞)."""
    return "".join(chr(0x10FFFF - ord(c)) if ord(c) < 0x10FFFF else c for c in (iso or ""))


_FB_LABEL = {"new": "접수됨", "ack": "확인함", "done": "해결됨", "wontfix": "고치지 않음"}


@router.get("")
async def list_notifications(user: User = Depends(get_current_user)) -> dict:
    """내 알림 — 공유받은 보고서 · 내 붐따 처리 상태 · 업데이트 공지.

    `type` 으로 구분한다. 프론트는 종류가 늘어도 같은 목록을 그린다.
    """
    shares, feedbacks, notices, briefs = await asyncio.gather(
        asyncio.to_thread(store.list_notifications, user.id),
        asyncio.to_thread(feedback_inbox.my_feedback, user.id),
        asyncio.to_thread(announcements.for_user, user.id),
        asyncio.to_thread(briefing.for_user, user.id),
    )

    items = [{
        "type": "report_share",
        "report_id": r["report_id"],
        "title": r.get("title") or r.get("question") or "보고서",
        "from_name": r.get("from_name") or "",
        "created_at": r["created_at"].isoformat() if r.get("created_at") else "",
        "seen": bool(r.get("seen_at")),
        "url": f"/api/reports/{r['report_id']}",
    } for r in shares]

    # 붐따 — 처리 전 것도 보여준다. "접수는 됐나" 를 알 수 없으면 제보가 끊긴다
    items += [{
        "type": "feedback",
        "feedback_id": f["id"],
        # 코멘트가 없으면 **어느 대화에서 눌렀는지**를 제목으로 쓴다. 붐따는 코멘트
        # 없이 누르는 경우가 대부분이라, 코멘트만 쓰면 "(내용 없음)" 이 줄줄이 뜬다
        "title": ((f.get("comment") or "").strip()
                  or (f.get("question") or "").strip()
                  or "이전 답변")[:80],
        "has_comment": bool((f.get("comment") or "").strip()),
        "status": f.get("status") or "new",
        "status_label": _FB_LABEL.get(f.get("status") or "new", "접수됨"),
        "note": (f.get("handled_note") or "").strip(),
        "created_at": f["created_at"].isoformat() if f.get("created_at") else "",
        "handled_at": f["handled_at"].isoformat() if f.get("handled_at") else "",
        "seen": not bool(f.get("unseen")),
        "url": "",
    } for f in feedbacks]

    items += [{
        "type": "announcement",
        "title": a.get("title") or "",
        "note": (a.get("body") or "").strip(),
        "created_at": a["created_at"].isoformat() if a.get("created_at") else "",
        "seen": not bool(a.get("unseen")),
        "url": "",
    } for a in notices]

    # 안 읽은 것이 먼저, 그 안에서 최신순 — 새 소식이 옛 항목에 묻히지 않게 한다
    # 데일리 브리핑 — 사용자가 묻지 않아도 먼저 가는 유일한 알림이다
    items += [{
        "type": "briefing",
        "title": b.get("title") or "",
        "note": (b.get("body") or "").strip(),
        "follow_up": b.get("follow_up") or "",
        "created_at": b["created_at"].isoformat() if b.get("created_at") else "",
        "seen": bool(b.get("seen_at")),
        "url": "",
    } for b in briefs]

    items.sort(key=lambda i: (i["seen"], _neg(i["created_at"])))
    return {"unseen": sum(1 for i in items if not i["seen"]), "items": items}


@router.post("/seen")
async def mark_all_seen(user: User = Depends(get_current_user)) -> dict:
    """세 종류를 함께 읽음 처리한다 — 사용자에게는 '알림' 하나다."""
    n = await asyncio.to_thread(store.mark_all_seen, user.id)
    await asyncio.to_thread(feedback_inbox.mark_my_feedback_seen, user.id)
    await asyncio.to_thread(announcements.mark_seen, user.id)
    await asyncio.to_thread(briefing.mark_seen, user.id)
    logger.info("notifications_marked_seen", user_id=user.id, shares=n)
    return {"marked": n}


@router.get("/briefing-setting")
async def briefing_setting(user: User = Depends(get_current_user)) -> dict:
    off = await asyncio.to_thread(briefing.is_opted_out, user.id)
    return {"opted_out": off}


@router.post("/briefing-setting")
async def set_briefing_setting(off: bool = True,
                               user: User = Depends(get_current_user)) -> dict:
    """브리핑 끄기/켜기.

    ⛔ 끌 수 없는 알림은 결국 **전체 알림을 무시하게** 만든다 — 그러면 공유·붐따 회신까지
       함께 묻힌다. 본인 설정만 바꾼다 (user_id 는 서버가 JWT 에서 정한다).
    """
    await asyncio.to_thread(briefing.set_opt_out, user.id, off)
    return {"opted_out": off}
