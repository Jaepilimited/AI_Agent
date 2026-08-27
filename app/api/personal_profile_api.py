# -*- coding: utf-8 -*-
"""내가 자주 묻는 것 — 첫 화면 제안용.

⚠️ **언제나 자기 것만** 돌려준다. 사용자 이메일은 JWT 에서 꺼내고 요청 본문에서는
   받지 않는다 — 남의 이력을 조회할 수 있는 입구를 아예 만들지 않는다.
   질문에는 담당 거래처·미출시 제품처럼 남이 보면 안 되는 말이 섞인다.
"""
from __future__ import annotations

import asyncio

import structlog
from fastapi import APIRouter, Depends

from app.api.auth_middleware import get_current_user
from app.db.models import User

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/personal", tags=["personal"])


@router.get("/suggestions")
async def my_suggestions(user: User = Depends(get_current_user)) -> dict:
    """내가 자주 물어본 질문과, 내가 자주 보는 축.

    ⚠️ 실패해도 화면이 죽으면 안 된다 — 제안은 **있으면 좋은 것**이지 없으면
       안 되는 것이 아니다. 빈 목록을 돌려주고 기본 칩이 그대로 뜨게 둔다.
    """
    from app.core.query_profile import for_user

    try:
        profile = await asyncio.to_thread(for_user, user.email)
    except Exception as e:
        logger.warning("suggestions_failed", error=str(e)[:160])
        return {"questions": [], "axes": {}}

    return {
        "questions": [
            {"text": q["text"], "n": q["n"]} for q in profile.get("questions", [])
        ],
        # 축은 화면에 바로 쓰지 않더라도 무엇을 근거로 제안했는지 설명할 수 있어야 한다
        "axes": profile.get("axes", {}),
    }
