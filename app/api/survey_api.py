# -*- coding: utf-8 -*-
"""만족도 설문 제출 — 별점 1~5 + 코멘트(선택), 또는 '나중에'.

노출 판정은 `/api/auth/me` 의 `survey_prompt` 가 한다 (방문을 기록하는 자리에서
함께 판정한다). 여기는 받은 것을 남기기만 한다.

⛔ **임계를 클라이언트가 정하지 못하게 한다.** 요청이 보낸 milestone 을 그대로
   믿으면 아무나 100일차 응답을 만들 수 있다. 서버가 다시 판정해서 대조한다.
"""
from __future__ import annotations

import asyncio
from typing import Optional

import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.auth_middleware import get_current_user
from app.db.models import User

logger = structlog.get_logger(__name__)

survey_router = APIRouter(prefix="/api/survey", tags=["survey"])

# 코멘트 상한 — 화면 입력은 더 짧지만, 서버가 자기 상한을 갖는다
_COMMENT_MAX = 2000


class SurveyIn(BaseModel):
    milestone: int
    rating: Optional[int] = Field(None, description="1~5, 생략하면 '나중에'")
    comment: str = ""


@survey_router.post("/reset")
async def reset_survey(user: User = Depends(get_current_user)):
    """내 설문 기록을 지운다 — **팝업을 다시 보기 위한 테스트용**.

    ⛔ 본인 것만이다. `user.id` 외에는 어디에서도 받지 않는다 — 대상을 요청이
       정하게 하면 남의 응답을 지우는 경로가 생긴다.
    """
    from app.core.satisfaction import pending_milestone, reset_for_user

    deleted = await asyncio.to_thread(reset_for_user, user.id)
    pending = await asyncio.to_thread(pending_milestone, user.id)
    logger.info("survey_reset_requested", user_id=user.id, deleted=deleted)
    return {"ok": True, "deleted": deleted, "survey_prompt": pending}


@survey_router.post("")
async def submit_survey(body: SurveyIn, user: User = Depends(get_current_user)):
    from app.core.satisfaction import (
        MILESTONES, pending_milestone, record_response,
    )

    if body.milestone not in MILESTONES:
        raise HTTPException(status_code=400, detail="unknown milestone")
    if body.rating is not None and body.rating not in (1, 2, 3, 4, 5):
        raise HTTPException(status_code=400, detail="rating must be 1..5")

    pending = await asyncio.to_thread(pending_milestone, user.id)
    if not pending or pending.get("milestone") != body.milestone:
        # 이미 답했거나 도달하지 않은 임계 — 조용히 성공으로 끝낸다.
        # (같은 화면을 두 탭에서 열면 자연히 생기는 상황이라 에러가 아니다)
        return {"ok": True, "stored": False}

    try:
        await asyncio.to_thread(
            record_response, user.id, body.milestone, body.rating,
            (body.comment or "")[:_COMMENT_MAX], pending.get("visit_days"),
        )
    except ValueError as e:
        logger.warning("survey_rejected", user_id=user.id, error=str(e)[:160])
        raise HTTPException(status_code=400, detail="잘못된 입력입니다")
    return {"ok": True, "stored": True}
