"""인증 사용자의 저장 질문 관리 API."""

from __future__ import annotations

import asyncio
from typing import Literal

import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.auth_middleware import get_current_user
from app.core import saved_questions
from app.db.models import User

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/saved-questions", tags=["saved-questions"])


class AddSavedQuestionRequest(BaseModel):
    question: str = Field(min_length=1, max_length=500)
    cadence: Literal["daily", "weekly", "monthly"]
    weekday: int | None = Field(default=None, ge=0, le=6)


class SetSavedQuestionEnabledRequest(BaseModel):
    enabled: bool


def _raise_internal(action: str, user_id: int, exc: Exception) -> None:
    logger.error(
        "saved_questions_api_failed",
        action=action,
        user_id=user_id,
        error_type=type(exc).__name__,
        error=str(exc)[:200],
    )
    # ⛔ DB·내부 경로가 든 예외 원문은 인증 사용자에게도 내보내지 않는다.
    raise HTTPException(
        status_code=500,
        detail="저장한 질문을 처리하지 못했습니다. 잠시 후 다시 시도해 주세요.",
    )


@router.get("")
async def list_saved_questions(user: User = Depends(get_current_user)) -> dict:
    try:
        rows = await asyncio.to_thread(saved_questions.list_for, user.id)
    except Exception as exc:
        _raise_internal("list", user.id, exc)
    return {"questions": rows}


@router.post("")
async def add_saved_question(
    request: AddSavedQuestionRequest,
    user: User = Depends(get_current_user),
) -> dict:
    try:
        result = await asyncio.to_thread(
            saved_questions.add,
            user.id,
            request.question,
            request.cadence,
            request.weekday,
        )
    except Exception as exc:
        _raise_internal("add", user.id, exc)
    if not result["ok"]:
        raise HTTPException(status_code=400, detail=result["reason"])
    return {"ok": True, "id": result["id"]}


@router.delete("/{question_id}")
async def remove_saved_question(
    question_id: int,
    user: User = Depends(get_current_user),
) -> dict:
    try:
        removed = await asyncio.to_thread(saved_questions.remove, user.id, question_id)
    except Exception as exc:
        _raise_internal("remove", user.id, exc)
    if not removed:
        # ⚠️ 없음과 남의 항목을 같은 응답으로 처리해야 소유 여부도 드러나지 않는다.
        raise HTTPException(status_code=404, detail="저장한 질문을 찾을 수 없습니다.")
    return {"removed": True}


@router.patch("/{question_id}")
async def set_saved_question_enabled(
    question_id: int,
    request: SetSavedQuestionEnabledRequest,
    user: User = Depends(get_current_user),
) -> dict:
    try:
        updated = await asyncio.to_thread(
            saved_questions.set_enabled,
            user.id,
            question_id,
            request.enabled,
        )
    except Exception as exc:
        _raise_internal("set_enabled", user.id, exc)
    if not updated:
        raise HTTPException(status_code=404, detail="저장한 질문을 찾을 수 없습니다.")
    return {"updated": True, "enabled": request.enabled}
