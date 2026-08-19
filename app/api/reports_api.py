# -*- coding: utf-8 -*-
"""보고서 API — 생성·목록·열람·공유.

**만든 사람과, 만든 사람이 지목한 사람만 연다.** admin 도 예외가 아니다 (2026-08-12 결정).
원가·마진·거래처별 FOC율이 들어가므로 매출 질문과 같은 수준으로 열지 않는다.

권한 판정은 `store.get_for_user(report_id, user_id)` 한 곳에서만 한다 —
조건을 여러 곳에 흩으면 한 곳만 고치고 뚫린다. 공유를 붙이면서도 이 원칙을 지켰다:
`get_for_user` 가 소유·공유를 함께 보고, 호출부는 그대로다.

공유 관리 엔드포인트는 **소유자만** 통한다. 판정은 SQL 안(`store.share_*`)에서 한다.
"""
from __future__ import annotations

import asyncio

import structlog
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from app.api.auth_middleware import get_current_user
from app.db.models import User
from app.reports import registry, service, share_ui, store

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


@router.get("/share-targets")
async def share_targets(q: str = "", user: User = Depends(get_current_user)) -> dict:
    """공유할 사람 찾기. 가입한 사용자만 나온다 (로그인해야 열 수 있으므로).

    ⚠️ 경로가 `/{report_id}` 보다 **위에** 있어야 한다 — 아래 두면 report_id 로 먹혀
       422 가 난다.
    """
    rows = await asyncio.to_thread(store.search_share_targets, q, user.id)
    return {"users": rows}


@router.get("/{report_id}/shares")
async def list_shares(report_id: int, user: User = Depends(get_current_user)) -> dict:
    rows = await asyncio.to_thread(store.share_list, report_id, user.id)
    return {"shares": rows}


class ShareRequest(BaseModel):
    user_ids: list[int]


@router.post("/{report_id}/shares")
async def add_shares(report_id: int, req: ShareRequest,
                     user: User = Depends(get_current_user)) -> dict:
    added = 0
    for uid in req.user_ids[:20]:
        if await asyncio.to_thread(store.share_add, report_id, user.id, int(uid)):
            added += 1
    rows = await asyncio.to_thread(store.share_list, report_id, user.id)
    if not rows and not added:
        # 소유자가 아니면 아무것도 걸리지 않는다 — 존재 여부를 알려주지 않는다
        raise HTTPException(status_code=404, detail="보고서를 찾을 수 없습니다.")
    return {"added": added, "shares": rows}


@router.delete("/{report_id}/shares/{target_id}")
async def remove_share(report_id: int, target_id: int,
                       user: User = Depends(get_current_user)) -> dict:
    ok = await asyncio.to_thread(store.share_remove, report_id, user.id, target_id)
    rows = await asyncio.to_thread(store.share_list, report_id, user.id)
    return {"removed": ok, "shares": rows}


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

    # 공유받은 보고서를 **열었으면** 알림에서 읽음 처리한다. 목록에서 눌렀든 채팅
    # 링크로 왔든 주소를 직접 쳤든 같은 자리를 지난다 — 여러 곳에 흩으면 한 경로에서만
    # 배지가 안 사라진다.
    if not row.get("is_owner"):
        await asyncio.to_thread(store.mark_seen, report_id, user.id)

    # 공유 막대는 **보는 사람마다 다르다** — 저장본에 굽지 않고 응답마다 끼운다
    html = share_ui.inject(html, report_id, bool(row.get("is_owner")),
                           row.get("owner_name") or "")

    # 다운로드·캐시 금지 — 브라우저 캐시에 원가·마진이 남지 않게 한다
    return HTMLResponse(content=html, headers={
        "Cache-Control": "no-store, no-cache, must-revalidate",
        "Pragma": "no-cache",
        "X-Robots-Tag": "noindex, nofollow",
    })
