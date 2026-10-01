"""잔디 백업 도우미 — 북마크를 나눠 주는 화면 하나뿐이다 (2026-10-01).

- `GET /jandi-backup`  화면 (세션 없으면 로그인으로)

⛔ 서버 API 가 **없다**. 대화·첨부는 각자의 잔디 탭에서 각자의 PC 폴더로 곧장 저장되고
   셀라 서버는 아무것도 받지 않는다. 북마크 본체는 `app/static/jandi-backup/jandi-backup.js`
   이고, 화면이 같은 출처에서 그 파일을 읽어 `javascript:` 주소에 통째로 넣는다.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse

from app.api.auth_middleware import _extract_user_id

router = APIRouter()


@router.get("/jandi-backup")
async def jandi_backup_page(request: Request):
    """⛔ 브라우저 주소로 여는 화면 — 세션이 없으면 원시 401 JSON 대신 로그인으로 보낸다
    (`/coa-finder`·`/sales-carryover` 와 같은 규칙)."""
    try:
        _extract_user_id(request)
    except HTTPException:
        return RedirectResponse(url="/login", status_code=302)
    return FileResponse("app/static/jandi_backup.html", media_type="text/html")
