# -*- coding: utf-8 -*-
"""보고서 API — 생성·목록·열람.

**열람은 본인이 만든 것만.** admin 도 남의 보고서를 열 수 없다 (2026-08-12 결정).
원가·마진·거래처별 FOC율이 들어가므로 매출 질문과 같은 수준으로 열지 않는다.

권한 판정은 `store.get_for_user(report_id, user_id)` 한 곳에서만 한다 —
조건을 여러 곳에 흩으면 한 곳만 고치고 뚫린다.
"""
from __future__ import annotations

import asyncio

import structlog
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from app.api.auth_middleware import get_current_user
from app.db.models import User
from app.reports import registry, service, store

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/reports", tags=["reports"])


class CreateReportRequest(BaseModel):
    question: str
    spec: str | None = None


@router.get("/specs")
async def list_specs(_: User = Depends(get_current_user)) -> dict:
    """만들 수 있는 보고서 종류. 없는 주제는 없다고 답하기 위해 프론트도 알아야 한다."""
    return {"specs": registry.available()}


@router.get("")
async def my_reports(user: User = Depends(get_current_user), limit: int = 30) -> dict:
    rows = await asyncio.to_thread(store.list_for_user, user.id, limit)
    return {"reports": rows}


@router.post("")
async def create_report(req: CreateReportRequest,
                        user: User = Depends(get_current_user)) -> dict:
    if req.spec and req.spec not in registry.SPECS:
        raise HTTPException(status_code=400, detail=f"없는 보고서 종류입니다: {req.spec}")
    try:
        result = await asyncio.to_thread(service.run, req.question, user.id, spec_id=req.spec)
    except Exception as e:
        logger.warning("report_create_failed", error=str(e)[:300], user_id=user.id)
        raise HTTPException(status_code=500, detail=f"보고서 생성에 실패했습니다: {e}")

    if not result:
        raise HTTPException(
            status_code=404,
            detail="이 주제로 만들 수 있는 보고서가 아직 없습니다. "
                   "만들 수 있는 종류는 /api/reports/specs 에 있습니다.",
        )
    return result


@router.get("/{report_id}/meta")
async def report_meta(report_id: int, user: User = Depends(get_current_user)) -> dict:
    row = await asyncio.to_thread(store.get_for_user, report_id, user.id)
    if not row:
        # 없는 것과 남의 것을 구분해 알려주지 않는다 (존재 여부 노출 방지)
        raise HTTPException(status_code=404, detail="보고서를 찾을 수 없습니다.")
    row.pop("payload_json", None)
    row.pop("html_path", None)
    return row


@router.get("/{report_id}", response_class=HTMLResponse)
async def read_report(report_id: int, user: User = Depends(get_current_user)) -> HTMLResponse:
    row = await asyncio.to_thread(store.get_for_user, report_id, user.id)
    if not row:
        logger.info("report_access_denied", report_id=report_id, user_id=user.id)
        raise HTTPException(status_code=404, detail="보고서를 찾을 수 없습니다.")

    html = await asyncio.to_thread(store.read_html, row)
    if not html:
        raise HTTPException(status_code=410, detail="보고서 본문이 남아 있지 않습니다.")

    # 다운로드·캐시 금지 — 브라우저 캐시에 원가·마진이 남지 않게 한다
    return HTMLResponse(content=html, headers={
        "Cache-Control": "no-store, no-cache, must-revalidate",
        "Pragma": "no-cache",
        "X-Robots-Tag": "noindex, nofollow",
    })
