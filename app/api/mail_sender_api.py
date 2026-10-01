"""메일 발송 전용 페이지 — 점검·미리보기·테스트 발송·작업. LLM 없음."""
from __future__ import annotations

import asyncio
import base64
import functools
from datetime import datetime, timezone

import requests
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from google.auth.exceptions import GoogleAuthError
from googleapiclient.errors import HttpError
from pydantic import BaseModel

from app.api.auth_middleware import _extract_user_id, get_current_user
from app.core import mail_google
from app.core.google_auth import GoogleAuthManager
from app.core.mail_mime import build_mime
from app.core.mail_sender import DOC_ID, TAB_DIR_SHEET, check, parse_rows, parse_tab_directory, pending
from app.core.mail_template import TemplateError, render, template_from_export_zip

router = APIRouter()
_auth = GoogleAuthManager()


def _credentials(email: str):
    return _auth.get_credentials(email)


def _google_errors(fn):
    """라우트 안에서 터진 구글 쪽 실패를 읽을 수 있는 한국어 detail 로 바꾼다 (원시 500 금지)."""
    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        try:
            return await fn(*args, **kwargs)
        except HTTPException:
            raise
        except RuntimeError as e:
            raise HTTPException(502, str(e) or "구글 호출에 실패했습니다") from e
        except HttpError as e:
            code = getattr(getattr(e, "resp", None), "status", "")
            raise HTTPException(502, f"구글 API 오류({code}): {getattr(e, 'reason', None) or e}") from e
        except (GoogleAuthError, requests.RequestException) as e:
            raise HTTPException(502, f"구글 연결에 실패했습니다: {e}") from e
    return wrapper


def _ready_creds(user):
    creds = _credentials(user.email)
    if creds is None:
        raise HTTPException(409, "구글 계정이 연결되어 있지 않습니다")
    try:
        missing = mail_google.missing_mail_scopes(creds)
    except RuntimeError as e:  # 일시 장애 — '권한 없음' 이 아니다
        raise HTTPException(503, str(e)) from e
    if missing:
        raise HTTPException(403, {"missing_scopes": missing,
                                  "message": "메일 발송 권한이 빠져 있습니다. '구글 다시 연결' 에서 모든 항목을 허용해 주세요"})
    return creds


def _check_sender(creds, sender: str) -> str:
    allowed = {s["email"].lower() for s in mail_google.send_as_addresses(creds)}
    if sender.lower() not in allowed:
        raise HTTPException(400, f"보내는 주소 '{sender}' 는 이 계정의 Gmail 에 등록된 주소가 아닙니다")
    return sender


def _load(creds, spreadsheet_id: str, sheet: str):
    rows, cols = parse_rows(mail_google.read_sheet(creds, spreadsheet_id, sheet))
    tab_dir, updated = parse_tab_directory(mail_google.read_sheet(creds, spreadsheet_id, TAB_DIR_SHEET))
    templates = {}
    for manager in {r.manager for r in pending(rows)}:
        tab_id = tab_dir.get(manager)
        if not tab_id:
            continue
        try:
            templates[manager] = template_from_export_zip(mail_google.export_tab_zip(creds, DOC_ID, tab_id))
        except (TemplateError, RuntimeError) as e:
            templates[manager] = e
    return rows, cols, templates, tab_dir, updated


async def _sent_today(email: str) -> int:
    return 0  # Task 7 에서 jobs.sent_today 로 바꾼다 (발송 기록 테이블이 생긴 뒤)


class SheetsIn(BaseModel):
    spreadsheet: str


class SheetIn(BaseModel):
    spreadsheet_id: str
    sheet: str


class RowIn(SheetIn):
    row: int
    sender: str = ""


@router.get("/mail-sender")
async def mail_sender_page(request: Request):
    try:
        _extract_user_id(request)
    except HTTPException:
        return RedirectResponse(url="/login", status_code=302)
    return FileResponse("app/static/mail_sender.html", media_type="text/html")


@router.get("/api/mail-sender/status")
async def status(user=Depends(get_current_user)):
    creds = _credentials(user.email)
    out = {"connected": creds is not None, "missing_scopes": [], "senders": [],
           "login_url": "/auth/google/login?feature=mail", "me": user.email}
    if creds is None:
        return out
    try:
        out["missing_scopes"] = await asyncio.to_thread(mail_google.missing_mail_scopes, creds)
        if not out["missing_scopes"]:
            out["senders"] = await asyncio.to_thread(mail_google.send_as_addresses, creds)
    except RuntimeError as e:  # 일시 장애 — 재연결 버튼 대신 메시지만 보인다
        out["missing_scopes"] = []
        out["senders"] = []
        out["error"] = str(e)
    return out


@router.post("/api/mail-sender/sheets")
@_google_errors
async def sheets(body: SheetsIn, user=Depends(get_current_user)):
    creds = _ready_creds(user)
    sid = mail_google.spreadsheet_id_from(body.spreadsheet)
    titles = await asyncio.to_thread(mail_google.sheet_titles, creds, sid)
    return {"spreadsheet_id": sid, "sheets": [t for t in titles if t.startswith("gmail_")]}


@router.post("/api/mail-sender/check")
@_google_errors
async def check_sheet(body: SheetIn, user=Depends(get_current_user)):
    creds = _ready_creds(user)
    rows, _, templates, tab_dir, updated = await asyncio.to_thread(_load, creds, body.spreadsheet_id, body.sheet)
    issues = check(pending(rows), tab_dir, updated, templates, datetime.now(timezone.utc),
                   sent_today=await _sent_today(user.email))
    return {"rows": [{"row": r.row, "manager": r.manager, "email": r.email, "status": r.status} for r in rows],
            "issues": [i.__dict__ for i in issues],
            "pending": len(pending(rows)),
            "errors": sum(1 for i in issues if i.level == "error")}


def _row_message(rows, templates, row: int):
    r = next((x for x in rows if x.row == row), None)
    if r is None:
        raise HTTPException(404, f"{row}행을 찾지 못했습니다")
    tpl = templates.get(r.manager)
    if tpl is None or isinstance(tpl, Exception):
        raise HTTPException(400, f"{r.manager} 템플릿 오류: {tpl}")
    subject, html = render(tpl, r.values)
    return r, tpl, subject, html


@router.post("/api/mail-sender/preview")
@_google_errors
async def preview(body: RowIn, user=Depends(get_current_user)):
    creds = _ready_creds(user)
    rows, _, templates, _, _ = await asyncio.to_thread(_load, creds, body.spreadsheet_id, body.sheet)
    _, tpl, subject, html = _row_message(rows, templates, body.row)
    for cid, (data, mime) in tpl.images.items():
        html = html.replace(f"cid:{cid}", f"data:{mime};base64," + base64.b64encode(data).decode())
    return {"subject": subject, "html": html}


@router.post("/api/mail-sender/test-send")
@_google_errors
async def test_send(body: RowIn, user=Depends(get_current_user)):
    creds = _ready_creds(user)
    sender = await asyncio.to_thread(_check_sender, creds, body.sender)
    rows, _, templates, _, _ = await asyncio.to_thread(_load, creds, body.spreadsheet_id, body.sheet)
    _, tpl, subject, html = _row_message(rows, templates, body.row)
    raw = build_mime(sender=sender, to=user.email, subject="[테스트] " + subject, html=html, images=tpl.images)
    return {"message_id": await asyncio.to_thread(mail_google.send_raw, creds, raw)}
