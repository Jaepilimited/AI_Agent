# -*- coding: utf-8 -*-
"""출근 브리핑의 노션 배송 설정 — 사용자 본인 것만 읽고 쓴다."""
from fastapi import APIRouter, Body, Depends, HTTPException

from app.api.auth_middleware import get_current_user
from app.core import notion_briefing
from app.db.models import User

router = APIRouter()


def _send_at_label(value) -> str:
    return notion_briefing.normalize_send_at(value).strftime("%H:%M")


@router.get("/api/personal-briefing/notion")
async def get_my_notion_target(user: User = Depends(get_current_user)) -> dict:
    notion_briefing.ensure_tables()
    row = notion_briefing.get_target(user.id)
    muted = notion_briefing.parse_muted(row.get("muted_sections") if row else "")
    base = {
        "send_time_choices": notion_briefing.SEND_TIME_CHOICES,
        "send_at": notion_briefing.DEFAULT_SEND_AT.strftime("%H:%M"),
        "sections": [
            {"key": key, "label": label, "group": group, "enabled": key not in muted}
            for key, label, group in notion_briefing.SECTIONS
        ],
    }
    if not row:
        return {**base, "registered": False, "enabled": False, "masked": "",
                "last_sent_at": "", "last_error": ""}
    return {
        **base,
        "registered": True,
        "enabled": bool(row["enabled"]),
        "send_at": _send_at_label(row.get("send_at")),
        # ⛔ 저장된 주소를 그대로 돌려주지 않는다.
        "masked": notion_briefing.mask(str(row["page_url"])),
        "last_sent_at": str(row["last_sent_at"] or ""),
        "last_error": str(row["last_error"] or ""),
    }


@router.put("/api/personal-briefing/notion")
async def put_my_notion_target(
    payload: dict = Body(...), user: User = Depends(get_current_user),
) -> dict:
    notion_briefing.ensure_tables()
    url = str(payload.get("page_url", "")).strip()
    if not url:
        # ⛔ 시각만 바꾸려는 사람에게 주소를 다시 붙여넣게 하지 마라 —
        #    서버는 주소를 가려서 내려주므로 되붙일 방법이 없다.
        #    비워서 보내면 이미 저장된 주소를 그대로 쓴다.
        row = notion_briefing.get_target(user.id)
        if not row:
            raise HTTPException(400, "노션 페이지 주소를 입력해 주세요.")
        url = str(row["page_url"])
    if not notion_briefing.is_valid_page_url(url):
        raise HTTPException(400, "노션 페이지 주소가 아닙니다.")

    send_at = payload.get("send_at")
    if send_at and str(send_at) not in notion_briefing.SEND_TIME_CHOICES:
        # ⛔ 목록에 없는 시각을 저장하면 그 사람 브리핑은 영영 오지 않는다.
        raise HTTPException(400, "받을 수 있는 시각이 아닙니다.")

    # ⚠️ `muted` 를 안 보낸 것과 빈 목록은 뜻이 다르다 — 없으면 "안 바꿈".
    muted = payload.get("muted") if "muted" in payload else None
    notion_briefing.set_target(
        user.id, url, enabled=bool(payload.get("enabled", True)),
        send_at=send_at, muted=muted)
    return {"ok": True}


@router.delete("/api/personal-briefing/notion")
async def delete_my_notion_target(user: User = Depends(get_current_user)) -> dict:
    notion_briefing.delete_target(user.id)
    return {"ok": True}


@router.post("/api/personal-briefing/notion/test")
async def test_my_notion_target(user: User = Depends(get_current_user)) -> dict:
    """지금 바로 한 건 보낸다 — 되는지 눈으로 보려고 누르는 버튼이다.

    ⚠️ 도착 시각을 붙이지 않는다 (지금 보내려는 것이므로).
    """
    from datetime import date

    from app.core import notion_export as nx

    row = notion_briefing.get_target(user.id)
    if not row:
        raise HTTPException(400, "먼저 노션 페이지를 등록해 주세요.")
    if not nx.is_enabled():
        raise HTTPException(503, "노션 저장이 아직 켜져 있지 않습니다.")
    try:
        target = nx.resolve_target(user.id, str(row["page_url"]))
        result = nx.save(target, f"{date.today()} 연결 확인",
                         "셀라에서 보낸 연결 확인입니다.", kind="브리핑")
    except nx.NotionError as exc:
        raise HTTPException(400, f"{exc}") from exc
    return {"ok": True, "url": result.url}
