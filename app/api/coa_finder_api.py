"""공유드라이브 COA·MSDS 찾기 — 페이지와 조회 API.

⛔ 서비스계정을 쓰지 않는다. 사용자 본인 OAuth 로만 읽어 드라이브 권한을 그대로 따른다.
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse

from app.api.auth_middleware import get_current_user
from app.core import coa_finder as cf
from app.core.google_auth import GoogleAuthManager

logger = logging.getLogger(__name__)
router = APIRouter()

_auth = GoogleAuthManager()
_MAX_ROWS = 500


def _credentials(email: str):
    return _auth.get_credentials(email)


@router.get("/coa-finder")
async def coa_finder_page(_: object = Depends(get_current_user)):
    return FileResponse("app/static/coa_finder.html", media_type="text/html")


@router.post("/api/coa-finder/search")
async def coa_finder_search(
    pasted: Optional[str] = Form(None),
    file: Optional[UploadFile] = File(None),
    user: object = Depends(get_current_user),
):
    if file is not None:
        rows_source = ("xlsx", await file.read())
    elif pasted:
        rows_source = ("pasted", pasted)
    else:
        raise HTTPException(400, "목록을 붙여넣거나 엑셀 파일을 올려주세요")

    try:
        rows = (cf.parse_xlsx(rows_source[1]) if rows_source[0] == "xlsx"
                else cf.parse_pasted(rows_source[1]))
    except cf.HeaderNotFound as exc:
        raise HTTPException(400, str(exc)) from exc

    if not rows:
        raise HTTPException(400, "행을 찾지 못했습니다 — 헤더 아래에 데이터가 있는지 확인하세요")
    if len(rows) > _MAX_ROWS:
        raise HTTPException(
            400, f"{len(rows)}행입니다. 한 번에 {_MAX_ROWS}행까지 처리합니다")

    creds = _credentials(user.email)
    if creds is None:
        # ⛔ 미연결을 '전부 없음' 으로 보여주면 조용한 오답이 된다
        raise HTTPException(409, "구글 계정이 연결되어 있지 않습니다. 먼저 연결해주세요")

    def _verdict(v: cf.Verdict) -> dict:
        return {
            "status": v.status,
            "note": v.note,
            "files": [{"id": f.id, "name": f.name, "link": f.web_link, "size": f.size}
                      for f in v.files],
        }

    def _stream():
        counts = {cf.FOUND: 0, cf.MANY: 0, cf.NONE: 0, cf.CHECK: 0}
        total = len(rows)
        for i, res in enumerate(cf.find_all(creds, rows), start=1):
            counts[res.coa.status] = counts.get(res.coa.status, 0) + 1
            payload = {
                "index": i, "total": total,
                "sku": res.row.sku, "description": res.row.description,
                "lot": res.row.lot,
                "coa": _verdict(res.coa), "msds": _verdict(res.msds),
            }
            yield f"event: row\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
        summary = {"total": total, "counts": counts}
        yield f"event: done\ndata: {json.dumps(summary, ensure_ascii=False)}\n\n"

    return StreamingResponse(_stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-store"})
