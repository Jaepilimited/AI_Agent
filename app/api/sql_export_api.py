# -*- coding: utf-8 -*-
"""채팅 표에서 잘린 SQL 조회 결과 전체를 CSV로 받는 다운로드 엔드포인트.

`app/core/sql_result_store.py` 에 잠시 보관된 결과를 내준다. 매출 데이터라
원가·거래처별 수치가 섞여 있을 수 있으므로 **저장한 사람만** 받을 수 있다 —
판정은 `store.get()` 한 곳에서만 한다(보고서 열람 `app/reports/reports_api.py`
와 같은 사상). 없음과 남의 토큰을 구분해 알려주지 않는다 — 둘 다 404.
"""
from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, HTTPException, Response

from app.api.auth_middleware import get_current_user
from app.core import sql_result_store
from app.db.models import User

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/sql-results", tags=["sql-results"])


@router.get("/{token}/csv")
async def download_csv(token: str, user: User = Depends(get_current_user)) -> Response:
    entry = sql_result_store.get(token, user.id)
    if not entry:
        # ⚠️ 만료·존재하지 않음·남의 토큰을 같은 응답으로 처리한다 — 존재 여부도 드러내지 않는다
        raise HTTPException(
            status_code=404,
            detail="다운로드할 결과를 찾을 수 없습니다. 링크가 만료되었을 수 있습니다 — 원래 질문을 다시 해주세요.",
        )
    body = sql_result_store.to_csv_bytes(entry["columns"], entry["rows"], entry.get("labels"))
    logger.info("sql_result_csv_downloaded", user_id=user.id, row_count=len(entry["rows"]))
    return Response(
        content=body,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="sql_result.csv"'},
    )
