# -*- coding: utf-8 -*-
"""출근 브리핑의 노션 배송 설정 — 사용자 본인 것만 읽고 쓴다."""
import asyncio
import re

from fastapi import APIRouter, Body, Depends, HTTPException

from app.api.auth_middleware import get_current_user
from app.core import notion_briefing
from app.core import notion_export as nx
from app.db.models import User

router = APIRouter()

#: 노션 오류 메시지에 섞여 나오는 데이터베이스·페이지 id (UUID, 대시 유무 무관).
#: ⛔ `page_url` 은 일부러 가려 내보내는데(`notion_briefing.mask`), 오류 메시지를
#:    그대로 돌려주면 그 id 가 그대로 샌다 — 내부 경로 마스킹과 같은 사상이다.
_ID_PATTERN = re.compile(
    r"\b[0-9a-fA-F]{8}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?"
    r"[0-9a-fA-F]{4}-?[0-9a-fA-F]{12}\b"
)


def _mask_ids(text: str) -> str:
    return _ID_PATTERN.sub(lambda m: f"…{m.group(0)[-4:]}", text)


def _send_at_label(value) -> str:
    """저장된 시각을 화면 표기로. 읽다가 깨져도 첫 회차로 답한다 — 설정 화면이
    통째로 안 뜨는 것보다 낫다 (잔디의 같은 이름 함수와 같은 가드)."""
    try:
        return notion_briefing.normalize_send_at(value).strftime("%H:%M")
    except ValueError:
        return notion_briefing.DEFAULT_SEND_AT.strftime("%H:%M")


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
        # ⚠️ `enabled` 는 "이 사용자가 켰는가" 다 — 이름이 겹치지 않게 기능 자체가
        #    꺼져 있는지는 따로 낸다. 채팅 등록(`notion_save._register_briefing`)은
        #    이미 이 관문을 보는데, 화면과 API 에는 없어서 토큰 없이도 등록되고
        #    행만 쌓이는데 영영 안 나가는 상태가 생겼다.
        "enabled_feature": nx.is_enabled(),
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
        # ⛔ 노션 오류 메시지에는 데이터베이스·페이지 id 가 그대로 들어 있다 —
        #    가리지 않으면 `page_url` 을 가린 뜻이 없어진다.
        "last_error": _mask_ids(str(row["last_error"] or "")),
    }


@router.put("/api/personal-briefing/notion")
async def put_my_notion_target(
    payload: dict = Body(...), user: User = Depends(get_current_user),
) -> dict:
    notion_briefing.ensure_tables()
    if not nx.is_enabled():
        # ⛔ 화면 가드가 있어도 관리자가 켜기 전까지는 등록해 봐야 대기열만 쌓이고
        #    영영 안 나간다 — `notion_save._register_briefing` 과 같은 관문이다.
        raise HTTPException(503, "노션 저장이 아직 켜져 있지 않습니다.")
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
    if send_at:
        try:
            # ⚠️ 문자열을 그대로 비교하지 않는다 — `8:00`·`08:00:00` 도 저장 계층은
            #    받는데 여기서만 거절하면 화면과 저장이 서로 다른 규칙을 갖는다.
            label = notion_briefing.normalize_send_at(send_at).strftime("%H:%M")
        except ValueError:
            raise HTTPException(400, "받을 수 있는 시각이 아닙니다.")
        if label not in notion_briefing.SEND_TIME_CHOICES:
            # ⛔ 목록에 없는 시각을 저장하면 그 사람 브리핑은 영영 오지 않는다.
            raise HTTPException(400, "받을 수 있는 시각이 아닙니다.")
        send_at = label

    # ⚠️ `muted` 를 안 보낸 것과 빈 목록은 뜻이 다르다 — 없으면 "안 바꿈".
    muted = payload.get("muted") if "muted" in payload else None
    notion_briefing.set_target(
        user.id, url, enabled=bool(payload.get("enabled", True)),
        send_at=send_at, muted=muted)
    if send_at is not None:
        # ⛔ 이걸 빼면 "고쳤는데 그대로" 가 된다 — 오늘 몫은 이전에 고른 시각에
        #    그대로 쓰이는데 화면은 새 시각을 말한다 (잔디와 같은 이유).
        notion_briefing.reschedule_pending(user.id, send_at)
    # ⚠️ 잔디와 같게 **갱신된 전체 상태**를 돌려준다 — 화면이 저장 직후
    #    한 번 더 GET 하지 않아도 되게.
    return await get_my_notion_target(user)


@router.delete("/api/personal-briefing/notion")
async def delete_my_notion_target(user: User = Depends(get_current_user)) -> dict:
    notion_briefing.delete_target(user.id)
    return {"ok": True}


@router.post("/api/personal-briefing/notion/test")
async def test_my_notion_target(user: User = Depends(get_current_user)) -> dict:
    """지금 바로 한 건 보낸다 — 되는지 눈으로 보려고 누르는 버튼이다.

    ⚠️ 도착 시각을 붙이지 않는다 (지금 보내려는 것이므로).
    ⛔ `resolve_target`·`save` 는 동기 httpx 호출이다(최대 5회 왕복 · 타임아웃 20초).
       프로덕션은 uvicorn 단일 프로세스라 여기서 막히면 **다른 모든 요청**이
       함께 멈춘다 — `asyncio.to_thread` 로 감싼다 (`notion_save.handle` 과 같은 규칙).
    """
    from datetime import date

    row = notion_briefing.get_target(user.id)
    if not row:
        raise HTTPException(400, "먼저 노션 페이지를 등록해 주세요.")
    if not nx.is_enabled():
        raise HTTPException(503, "노션 저장이 아직 켜져 있지 않습니다.")
    try:
        target = await asyncio.to_thread(
            nx.resolve_target, user.id, str(row["page_url"]))
        result = await asyncio.to_thread(
            nx.save, target, f"{date.today()} 연결 확인",
            "셀라에서 보낸 연결 확인입니다.", kind="브리핑")
    except nx.NotionError as exc:
        raise HTTPException(400, f"{exc}") from exc
    return {"ok": True, "url": result.url}
