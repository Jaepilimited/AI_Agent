"""메일 발송 전용 페이지 — 점검·미리보기·테스트 발송·작업. LLM 없음."""
from __future__ import annotations

import asyncio
import base64
import binascii
import functools
import re
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pymysql
import requests
from dbutils.pooled_db import PooledDBError
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from google.auth.exceptions import GoogleAuthError, RefreshError
from googleapiclient.errors import HttpError
from pydantic import BaseModel

from app.api.auth_middleware import _extract_user_id, get_current_user
from app.core import mail_google
from app.core import mail_sender_jobs as jobs
from app.core.google_auth import GoogleAuthManager
from app.core.mail_mime import build_mime
from app.core.mail_sender import (
    MAIL_SPREADSHEETS, MAX_MESSAGE_BYTES, TAB_DIR_SHEET, template_cache_index_id, Issue, check, ensure_message_size,
    estimate_size, first_subjects, parse_rows, parse_tab_directory, pending,
)
from app.core.mail_template import MailTemplate, TemplateError, render, template_from_export_zip

router = APIRouter()
_EXPIRED = "구글 연결이 만료되었습니다 — 다시 연결해 주세요"
_DB_DOWN = "발송 기록 DB 에 연결하지 못했습니다 — 잠시 뒤 다시 시도"
_auth = GoogleAuthManager()
_NO_TAB_DIR = f"이 스프레드시트에 `{TAB_DIR_SHEET}` 이 없습니다 — gmailv2 시트(탭 목록이 있는 시트)를 쓰세요"
# 공통 첨부 — 화면이 base64 JSON 으로 보낸다 (본문 ~20MB). 수신자별 Drive 첨부·이미지와 합쳐도 24MB 안에 들게 15MB.
MAX_COMMON_BYTES = 15 * 1024 * 1024
MAX_COMMON_FILES = 10
_BAD_NAME_CHARS = re.compile(r"[/\\\x00-\x1f\x7f]")


def _credentials(email: str):
    return _auth.get_credentials(email)


def _google_errors(fn):
    """라우트 안에서 터진 구글·발송 기록 DB 쪽 실패를 읽을 수 있는 한국어 detail 로 바꾼다 (원시 500 금지)."""
    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        try:
            return await fn(*args, **kwargs)
        except HTTPException:
            raise
        except (pymysql.MySQLError, PooledDBError) as e:
            raise HTTPException(503, _DB_DOWN) from e
        except RefreshError as e:
            raise HTTPException(409, _EXPIRED) from e
        except RuntimeError as e:
            raise HTTPException(502, str(e) or "구글 호출에 실패했습니다") from e
        except HttpError as e:
            code = getattr(getattr(e, "resp", None), "status", "")
            raise HTTPException(502, f"구글 API 오류({code}): {getattr(e, 'reason', None) or e}") from e
        except (GoogleAuthError, requests.RequestException) as e:
            raise HTTPException(502, f"구글 연결에 실패했습니다: {e}") from e
        except ValueError as e:   # 시트 양식 문제 등 — 사람이 고칠 수 있는 것
            raise HTTPException(400, str(e) or "입력값을 확인해 주세요") from e
    return wrapper


def _ready_creds(user):
    creds = _credentials(user.email)
    if creds is None:
        raise HTTPException(409, "구글 계정이 연결되어 있지 않습니다")
    try:
        missing = mail_google.missing_mail_scopes(creds)
    except RefreshError as e:
        raise HTTPException(409, _EXPIRED) from e
    except RuntimeError as e:  # 일시 장애 — '권한 없음' 이 아니다
        raise HTTPException(503, str(e)) from e
    if missing:
        raise HTTPException(403, {"missing_scopes": missing,
                                  "message": "메일 발송 권한이 빠져 있습니다. '구글 다시 연결' 에서 모든 항목을 허용해 주세요"})
    return creds


def _check_sender(creds, sender: str) -> tuple[str, str]:
    """(주소, 표시 이름) — Gmail sendAs 에 등록된 주소만."""
    for s in mail_google.send_as_addresses(creds):
        if s["email"].lower() == sender.lower():
            return sender, s.get("display") or ""
    raise HTTPException(400, f"보내는 주소 '{sender}' 는 이 계정의 Gmail 에 등록된 주소가 아닙니다")


def _load(creds, spreadsheet_id: str, sheet: str):
    rows, cols = parse_rows(mail_google.read_sheet(creds, spreadsheet_id, sheet))
    try:
        tab_values = mail_google.read_sheet(creds, spreadsheet_id, TAB_DIR_SHEET)
    except HttpError as e:  # 없는 시트 이름이면 구글은 400 'Unable to parse range' 를 준다
        if getattr(getattr(e, "resp", None), "status", None) == 400:
            raise HTTPException(400, _NO_TAB_DIR) from e
        raise
    tab_dir, updated = parse_tab_directory(tab_values)
    templates = {}
    managers = {r.manager for r in pending(rows)}
    cache = mail_google.read_template_cache_index(creds, template_cache_index_id()) if managers else {}
    for manager in managers:
        tab_id = tab_dir.get(manager)
        if not tab_id:
            continue
        try:
            templates[manager] = template_from_export_zip(mail_google.cached_tab_zip(creds, cache, tab_id))
        except (TemplateError, RuntimeError, requests.RequestException, zipfile.BadZipFile) as e:
            templates[manager] = e
    return rows, cols, templates, tab_dir, updated


async def _sent_today(email: str) -> int:
    return await asyncio.to_thread(jobs.sent_today, email)


class SheetsIn(BaseModel):
    spreadsheet: str


class SheetIn(BaseModel):
    spreadsheet_id: str
    sheet: str


class AttachmentIn(BaseModel):
    name: str
    mime: str = ""
    data: str  # base64


class CheckIn(SheetIn):
    common_bytes: int = 0  # 화면이 고른 공통 첨부 총 바이트 — 용량 판정용


class RowIn(SheetIn):
    row: int
    sender: str = ""
    attachments: list[AttachmentIn] = []


def _decode_attachments(items: list[AttachmentIn]) -> list[tuple[str, str, bytes]]:
    if len(items) > MAX_COMMON_FILES:
        raise HTTPException(400, f"공통 첨부는 {MAX_COMMON_FILES}개까지입니다 ({len(items)}개)")
    out, total = [], 0
    for a in items:
        try:
            data = base64.b64decode(a.data, validate=True)
        except (binascii.Error, ValueError) as e:
            raise HTTPException(400, f"첨부 '{a.name}' 을 읽지 못했습니다 (base64 오류) — 다시 골라 주세요") from e
        total += len(data)
        if total > MAX_COMMON_BYTES:
            raise HTTPException(400, f"공통 첨부 합계가 {MAX_COMMON_BYTES // 1024 // 1024}MB 를 넘습니다 — 파일을 줄여 주세요")
        name = _BAD_NAME_CHARS.sub("", a.name).strip() or "첨부파일"  # 경로·헤더 끼워 넣기 차단
        out.append((name, a.mime.strip() or "application/octet-stream", data))
    return out


def _drive_metas(creds, ids: set[str]) -> dict:
    """파일 ID → meta 또는 예외. 같은 ID 는 한 번만, 몇 개씩 동시에 (행이 많으면 순차로는 너무 느리다)."""
    def one(fid):
        try:
            return fid, mail_google.drive_meta(creds, fid)
        except (RuntimeError, requests.RequestException) as e:
            return fid, e
    if not ids:
        return {}
    with ThreadPoolExecutor(max_workers=min(8, len(ids))) as ex:
        return dict(ex.map(one, sorted(ids)))


def _attachment_issues(creds, targets, templates: dict, common_bytes: int = 0) -> list[Issue]:
    """수신자별 첨부(Drive 링크)·공통 첨부 점검 — 링크 형식, 접근 권한·종류, 합친 용량."""
    metas = _drive_metas(creds, {f for r in targets for f in r.attachments})
    issues: list[Issue] = []
    for r in targets:
        if r.attach_bad:
            issues.append(Issue(r.row, "error", f"첨부 링크를 알아볼 수 없습니다: {', '.join(r.attach_bad)}"))
        size = 0
        for fid in r.attachments:
            m = metas[fid]
            try:
                if isinstance(m, Exception):
                    raise m
                mail_google.attachment_name(m)
            except (RuntimeError, requests.RequestException) as e:
                issues.append(Issue(r.row, "error", f"첨부 오류: {e} ({fid})"))
                continue
            size += int(m.get("size") or 0)  # 구글 문서류는 size 가 없다 — PDF 크기는 미리 알 수 없다
        tpl = templates.get(r.manager)
        if not isinstance(tpl, MailTemplate) or (not size and not common_bytes):
            continue
        base = estimate_size(tpl)
        total = base + (common_bytes + size) * 4 // 3
        if base <= MAX_MESSAGE_BYTES < total:  # 템플릿만으로 넘는 경우는 check() 가 이미 알린다
            issues.append(Issue(r.row, "error", f"메일 용량 초과 예상 ({total // 1024 // 1024} MB) — 첨부를 줄여 주세요"))
    return issues


def _row_files(creds, r) -> list[tuple[str, str, bytes]]:
    if r.attach_bad:
        raise HTTPException(400, f"첨부 링크를 알아볼 수 없습니다: {', '.join(r.attach_bad)}")
    return [mail_google.drive_attachment(creds, fid) for fid in r.attachments]


def _attachment_names(creds, r) -> list[str]:
    names = []
    for fid in r.attachments:
        try:
            names.append(mail_google.attachment_name(mail_google.drive_meta(creds, fid)))
        except (RuntimeError, requests.RequestException) as e:  # 미리보기는 막지 않는다 — 점검이 오류로 잡는다
            names.append(f"{fid} (오류: {e})")
    return names


@router.get("/mail-sender")
async def mail_sender_page(request: Request):
    try:
        _extract_user_id(request)
    except HTTPException:
        return RedirectResponse(url="/login", status_code=302)
    return FileResponse("app/static/mail_sender.html", media_type="text/html")


@router.get("/api/mail-sender/status")
async def status(user=Depends(get_current_user)):
    creds = await asyncio.to_thread(_credentials, user.email)  # 토큰 파일 읽기·갱신 — 이벤트 루프 밖에서
    out = {"connected": creds is not None, "missing_scopes": [], "senders": [],
           "login_url": "/auth/google/login?feature=mail", "me": user.email,
           "spreadsheets": MAIL_SPREADSHEETS}
    if creds is None:
        return out
    try:
        out["missing_scopes"] = await asyncio.to_thread(mail_google.missing_mail_scopes, creds)
        if not out["missing_scopes"]:
            out["senders"] = await asyncio.to_thread(mail_google.send_as_addresses, creds)
    except RefreshError:  # 토큰 폐기 — 재연결이 필요하다
        out["connected"] = False
        out["missing_scopes"] = []
        out["senders"] = []
        out["error"] = _EXPIRED
    except RuntimeError as e:  # 일시 장애 — 재연결 버튼 대신 메시지만 보인다
        out["missing_scopes"] = []
        out["senders"] = []
        out["error"] = str(e)
    except HttpError as e:  # 보내는 주소 목록 실패 (권한·일시 장애) — 원시 500 대신 메시지
        code = getattr(getattr(e, "resp", None), "status", "")
        out["senders"] = []
        out["error"] = f"보내는 주소 목록을 읽지 못했습니다 — 구글 API 오류({code}): {getattr(e, 'reason', None) or e}"
    except (GoogleAuthError, requests.RequestException) as e:
        out["senders"] = []
        out["error"] = f"구글 연결에 실패했습니다: {e}"
    return out


@router.post("/api/mail-sender/sheets")
@_google_errors
async def sheets(body: SheetsIn, user=Depends(get_current_user)):
    creds = await asyncio.to_thread(_ready_creds, user)
    sid = mail_google.spreadsheet_id_from(body.spreadsheet)
    gids = await asyncio.to_thread(mail_google.sheet_gids, creds, sid)
    titles = [t for t in gids if t.startswith("gmail_")]
    return {"spreadsheet_id": sid, "sheets": titles, "gids": {t: gids[t] for t in titles}}


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
    creds = await asyncio.to_thread(_ready_creds, user)
    rows, _, templates, _, _ = await asyncio.to_thread(_load, creds, body.spreadsheet_id, body.sheet)
    r, tpl, subject, html = _row_message(rows, templates, body.row)
    for cid, (data, mime) in tpl.images.items():
        html = html.replace(f"cid:{cid}", f"data:{mime};base64," + base64.b64encode(data).decode())
    names = await asyncio.to_thread(_attachment_names, creds, r)
    return {"subject": subject, "html": html, "attachments": names}


@router.post("/api/mail-sender/test-send")
@_google_errors
async def test_send(body: RowIn, user=Depends(get_current_user)):
    common = _decode_attachments(body.attachments)
    creds = await asyncio.to_thread(_ready_creds, user)
    sender, sender_name = await asyncio.to_thread(_check_sender, creds, body.sender)
    rows, _, templates, _, _ = await asyncio.to_thread(_load, creds, body.spreadsheet_id, body.sheet)
    r, tpl, subject, html = _row_message(rows, templates, body.row)
    files = await asyncio.to_thread(_row_files, creds, r)
    raw = build_mime(sender=sender, sender_name=sender_name, to=user.email, subject="[테스트] " + subject, html=html,
                     images=tpl.images, attachments=[*common, *files])
    try:
        ensure_message_size(raw)
    except RuntimeError as e:  # 사람이 첨부를 줄여서 고칠 문제 — 502 가 아니다
        raise HTTPException(400, str(e)) from e
    return {"message_id": await asyncio.to_thread(mail_google.send_raw, creds, raw)}


def _rowlist(rows: list[int], limit: int = 10) -> str:
    return ", ".join(map(str, rows[:limit])) + ("…" if len(rows) > limit else "")


def _split_targets(rows, history: list[dict], me: str) -> tuple[list, list[Issue]]:
    """발송 대상 = G≠O 이면서 막는 발송 기록이 없는 행 (jobs.blocking_record: 보냄=이메일,
    발송 중·불확실=행 또는 이메일). 누구의 기록이든 뺀다.

    ⛔ /check 와 시작 경로가 이 함수 하나를 쓴다 — 두 숫자가 달라지면 확인 창 숫자가 거짓이 된다.
    """
    targets, sent, mine, others = [], [], [], {}
    per_row: list[Issue] = []
    for r in pending(rows):
        b = jobs.blocking_record(history, r.row, r.email)
        if b is None:
            targets.append(r)
            continue
        hit = f"({b['by']} 일치)"
        if b["status"] == "sent":
            sent.append(r.row)
            per_row.append(Issue(r.row, "warn", f"발송 기록 있음 — 다시 보내지 않습니다 {hit}"))
        elif b["owner"] == me:
            mine.append(r.row)
            per_row.append(Issue(r.row, "warn", f"발송 여부 불확실 — 보낸편지함 확인 후 '확인했음' {hit}"))
        else:
            others.setdefault(b["owner"], []).append(r.row)
            per_row.append(Issue(r.row, "warn", f"다른 사람({b['owner']})의 작업에서 발송 여부 불확실 — "
                                                f"그 사람이 확인해야 합니다 {hit}"))
    parts = []
    if sent:
        parts.append(f"발송 기록 있음 {len(sent)}행({_rowlist(sent)}) — 다시 보내지 않습니다")
    if mine:
        parts.append(f"발송 여부 불확실 {len(mine)}행({_rowlist(mine)}) — 보낸편지함 확인 후 '확인했음'")
    for owner, rs in others.items():
        parts.append(f"다른 사람({owner})의 작업에서 발송 여부 불확실 {len(rs)}행({_rowlist(rs)}) — 그 사람이 확인해야 합니다")
    issues = [Issue(None, "warn", " · ".join(parts))] if parts else []
    return targets, issues + per_row


async def _targets(spreadsheet_id: str, sheet: str, rows, me: str) -> tuple[list, list[Issue], list[dict]]:
    history = await asyncio.to_thread(jobs.sheet_history, spreadsheet_id, sheet)
    targets, issues = _split_targets(rows, history, me)
    return targets, issues, jobs.own_unknown_jobs(history, me)


@router.post("/api/mail-sender/check")
@_google_errors
async def check_sheet(body: CheckIn, user=Depends(get_current_user)):
    creds = await asyncio.to_thread(_ready_creds, user)
    rows, _, templates, tab_dir, updated = await asyncio.to_thread(_load, creds, body.spreadsheet_id, body.sheet)
    targets, excluded, unknown_jobs = await _targets(body.spreadsheet_id, body.sheet, rows, user.email)
    issues = excluded + check(targets, tab_dir, updated, templates, datetime.now(timezone.utc),
                              sent_today=await _sent_today(user.email))
    issues += await asyncio.to_thread(_attachment_issues, creds, targets, templates, max(0, body.common_bytes))
    return {"rows": [{"row": r.row, "manager": r.manager, "email": r.email, "status": r.status,
                      "attachments": len(r.attachments)} for r in rows],
            "issues": [i.__dict__ for i in issues],
            "pending": len(targets),
            "subjects": first_subjects(targets, templates),  # 확인 창: 담당자 탭별 첫 대상 행의 제목
            "unknown_jobs": unknown_jobs,  # 본인 작업의 '확인했음' 대상 (중단된 작업 포함)
            "errors": sum(1 for i in issues if i.level == "error")}


# ---------------------------------------------------------------------------
# 발송 작업 — ⛔ 확인 창(confirm_count) 없이는 시작하지 않는다
# ---------------------------------------------------------------------------
class JobIn(SheetIn):
    sender: str
    confirm_count: int
    attachments: list[AttachmentIn] = []


@router.post("/api/mail-sender/jobs")
@_google_errors
async def start_job(body: JobIn, user=Depends(get_current_user)):
    common = _decode_attachments(body.attachments)
    creds = await asyncio.to_thread(_ready_creds, user)
    sender, sender_name = await asyncio.to_thread(_check_sender, creds, body.sender)  # 시작 시점에 다시 검증
    rows, cols, templates, tab_dir, updated = await asyncio.to_thread(_load, creds, body.spreadsheet_id, body.sheet)
    targets, _, _ = await _targets(body.spreadsheet_id, body.sheet, rows, user.email)
    issues = check(targets, tab_dir, updated, templates, datetime.now(timezone.utc),
                   sent_today=await _sent_today(user.email))
    issues += await asyncio.to_thread(_attachment_issues, creds, targets, templates,
                                      sum(len(d) for _, _, d in common))
    errors = [i for i in issues if i.level == "error"]
    if errors:
        raise HTTPException(400, f"점검 오류 {len(errors)}건이 있어 시작하지 않습니다: {errors[0].message}")
    if len(targets) != body.confirm_count:  # 확인 창 이후 시트가 바뀌었다
        raise HTTPException(409, f"대상이 바뀌었습니다 ({body.confirm_count} → {len(targets)}명). 다시 점검하세요")
    try:  # 저장소가 잠금 안에서 발송 기록을 다시 따지고, 숫자가 달라지면 TargetsChanged
        job_id = await asyncio.to_thread(jobs.create_job, user.email, body.spreadsheet_id, body.sheet, sender,
                                         targets, body.confirm_count)
    except RuntimeError as e:
        raise HTTPException(409, str(e)) from e
    jobs.start_job_thread(job_id, creds, cols, templates, sender_name=sender_name, attachments=common)
    return {"job_id": job_id}


def _jsonable(j: dict) -> dict:
    return {k: (str(v) if isinstance(v, datetime) else v) for k, v in j.items()}


@router.get("/api/mail-sender/jobs/active")  # ⛔ /jobs/{job_id} 보다 먼저 선언 (경로 매칭 순서)
@_google_errors
async def active(user=Depends(get_current_user)):
    j = await asyncio.to_thread(jobs.active_job, user.email)
    return {"job": _jsonable(j) if j else None}


@router.get("/api/mail-sender/jobs/{job_id}")
@_google_errors
async def job_status(job_id: int, user=Depends(get_current_user)):
    j = await asyncio.to_thread(jobs.get_job, job_id, user.email)
    if not j:
        raise HTTPException(404, "작업이 없습니다")
    return _jsonable(j)


@router.post("/api/mail-sender/jobs/{job_id}/stop")
@_google_errors
async def stop(job_id: int, user=Depends(get_current_user)):
    return {"ok": await asyncio.to_thread(jobs.request_stop, job_id, user.email)}


@router.post("/api/mail-sender/jobs/{job_id}/ack-unknown")
@_google_errors
async def ack_unknown(job_id: int, user=Depends(get_current_user)):
    """작업 주인이 보낸편지함을 확인했다 — 불확실 행(과 끝난 작업에 남은 '발송 중' 행)을 '확인됨' 으로.
    나간 행은 사용자가 시트에 O 를 적는다 (O 가 없는 확인됨 행은 다음 작업의 대상이 된다)."""
    n = await asyncio.to_thread(jobs.ack_unknown, job_id, user.email)
    if n is None:
        raise HTTPException(404, "작업이 없습니다")
    return {"acknowledged": n}
