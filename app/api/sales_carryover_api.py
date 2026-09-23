"""매출 이월 확인 — 페이지와 조회 API (인증서류 찾기와 같은 구조).

- `GET /sales-carryover`                 화면 (세션 없으면 로그인으로)
- `GET /api/sales-carryover/snapshots`   고를 수 있는 백업 날짜 목록
- `GET /api/sales-carryover/compare`     두 날짜 비교 (`a`·`b`=YYYYMMDD, `month`=YYYY-MM 선택)

접근: 관리자이거나 브랜드 그룹에 **SK** 가 있는 사람. 이 화면은 스킨천사(SK·CBT) B2B
거래처별 매출을 그대로 보여 주므로 채팅과 같은 브랜드 관문을 지킨다.
"""
from __future__ import annotations

import asyncio
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse

from app.api.auth_middleware import GROUP_ASSIGNMENT_MESSAGE, _extract_user_id, get_current_user
from app.core import sales_carryover as sc
from app.db.mariadb import fetch_one

router = APIRouter()

_NO_ACCESS = "스킨천사(SK) 매출 열람 권한이 있는 그룹에서만 볼 수 있습니다. 관리자에게 문의해 주세요."


def _brand_access(user) -> None:
    """채팅(`routes.py`)과 같은 판정. 관리자 통과 · 플래그 1 인데 그룹 없음 → 403 ·
    그룹이 있으면 SK 가 들어 있어야 한다 · 플래그 0 이고 그룹 없음(옛 계정) → 통과."""
    if getattr(user, "role", "") == "admin":
        return
    row = fetch_one(
        "SELECT u.requires_group_assignment, "
        "(SELECT GROUP_CONCAT(DISTINCT g.brand_filter) FROM user_groups ug "
        "JOIN access_groups g ON g.id=ug.group_id WHERE ug.ad_user_id=u.ad_user_id "
        "AND g.brand_filter IS NOT NULL AND g.brand_filter<>'') AS brand_filter "
        "FROM users u WHERE u.id = %s LIMIT 1",
        (user.id,),
    ) or {}
    brands = {b.strip().upper() for b in str(row.get("brand_filter") or "").split(",") if b.strip()}
    if not brands:
        if row.get("requires_group_assignment"):
            raise HTTPException(403, GROUP_ASSIGNMENT_MESSAGE)
        return
    if "SK" not in brands:
        raise HTTPException(403, _NO_ACCESS)


@router.get("/sales-carryover")
async def sales_carryover_page(request: Request):
    """⛔ 브라우저 주소로 여는 화면 — 세션이 없으면 원시 401 JSON 대신 로그인으로 보낸다
    (`/coa-finder` 와 같은 규칙)."""
    try:
        _extract_user_id(request)
    except HTTPException:
        return RedirectResponse(url="/login", status_code=302)
    return FileResponse("app/static/sales_carryover.html", media_type="text/html")


@router.get("/api/sales-carryover/snapshots")
async def sales_carryover_snapshots(user=Depends(get_current_user)):
    _brand_access(user)
    try:
        dates = await asyncio.to_thread(sc.list_snapshots)
    except Exception as exc:  # noqa: BLE001 - 목록을 못 읽으면 빈 화면이 아니라 이유를 준다
        raise HTTPException(503, f"백업 테이블 목록을 읽지 못했습니다: {type(exc).__name__}") from None
    return {"dates": dates, "prefix": sc.SNAPSHOT_PREFIX}


@router.get("/api/sales-carryover/compare")
async def sales_carryover_compare(a: str, b: str, month: Optional[str] = None,
                                  user=Depends(get_current_user)):
    _brand_access(user)
    try:
        result = await asyncio.to_thread(sc.compare, a, b, month)
    except sc.CarryoverError as exc:
        raise HTTPException(400, str(exc)) from None
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(503, f"조회에 실패했습니다: {type(exc).__name__}") from None
    return result.as_dict()
