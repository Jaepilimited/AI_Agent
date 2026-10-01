"""메일 발송용 구글 호출 — 전부 사용자 본인 토큰. WAS 프록시는 HTTPS_PROXY 환경변수로 탄다."""
from __future__ import annotations

import io
import json
import re

import requests
from google.auth.transport.requests import AuthorizedSession, Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

from app.core.google_auth import REQUIRED_MAIL_PAGE_SCOPES


def _fresh(creds):
    if not creds.valid:
        creds.refresh(Request())
    return creds


def granted_scopes(creds) -> set[str]:
    try:
        r = requests.get("https://oauth2.googleapis.com/tokeninfo",
                         params={"access_token": _fresh(creds).token}, timeout=20)
    except requests.RequestException as e:
        raise RuntimeError("구글 권한 확인 실패 (네트워크) — 잠시 뒤 다시 시도하세요") from e
    if not r.ok:
        raise RuntimeError(f"구글 권한 확인 실패 (HTTP {r.status_code}) — 잠시 뒤 다시 시도하세요")
    return set((r.json().get("scope") or "").split())


def missing_mail_scopes(creds) -> list[str]:
    """메일 페이지에 필요한 스코프(발송·시트 + 보내는 주소 목록·탭 내보내기) 중 빠진 것."""
    have = granted_scopes(creds)
    return [s for s in REQUIRED_MAIL_PAGE_SCOPES if s not in have]


def spreadsheet_id_from(url_or_id: str) -> str:
    m = re.search(r"/spreadsheets/d/([A-Za-z0-9_-]+)", url_or_id)
    return m.group(1) if m else url_or_id.strip()


def export_tab_zip(creds, doc_id: str, tab_id: str) -> bytes:
    r = AuthorizedSession(_fresh(creds)).get(
        f"https://docs.google.com/document/d/{doc_id}/export",
        params={"format": "zip", "tab": tab_id}, timeout=180)
    if r.status_code == 404:
        raise RuntimeError("문서나 탭을 찾을 수 없습니다 (탭 목록이 낡았을 수 있습니다)")
    if r.status_code in (401, 403):
        raise RuntimeError("이 문서에 접근 권한이 없습니다 — 문서 공유 설정을 확인하세요")
    if r.status_code != 200:
        raise RuntimeError(f"탭 내보내기 실패 (HTTP {r.status_code})")
    if r.content[:2] != b"PK":
        raise RuntimeError("구글이 로그인/권한 페이지를 돌려줬습니다 (zip 이 아님) — 구글 다시 연결")
    return r.content


def drive_download(creds, file_id: str) -> bytes:
    """Drive 파일 내용. ⚠️ `alt=media` 는 www.googleapis.com 에서 바로 내려와 WAS 프록시를 통과한다
    (2026-10-01 실측). Docs 내보내기(`docs.google.com/.../export`)는 `*.googleusercontent.com` 으로
    넘겨져 프록시에 막힌다 — 그래서 템플릿은 캐시(아래)에서 읽는다."""
    r = AuthorizedSession(_fresh(creds)).get(
        f"https://www.googleapis.com/drive/v3/files/{file_id}", params={"alt": "media"}, timeout=180)
    if r.status_code == 404:
        raise RuntimeError("템플릿 캐시 파일을 찾을 수 없습니다 — 10분 뒤 다시 점검하세요")
    if r.status_code in (401, 403):
        raise RuntimeError("템플릿 캐시에 접근 권한이 없습니다 — 회사 구글 계정으로 연결했는지 확인하세요")
    if r.status_code != 200:
        raise RuntimeError(f"템플릿 캐시 읽기 실패 (HTTP {r.status_code})")
    return r.content


# 수신자별 첨부 — 구글 문서류는 PDF 로 내보내 붙인다. ⚠️ 받기·내보내기 모두 www.googleapis.com 의 Drive API 로만
# (docs.google.com 내보내기는 *.googleusercontent.com 으로 넘겨져 WAS 프록시에 막힌다).
_DRIVE_FILES = "https://www.googleapis.com/drive/v3/files/"
GOOGLE_EXPORTABLE = frozenset({"application/vnd.google-apps.document", "application/vnd.google-apps.spreadsheet",
                               "application/vnd.google-apps.presentation", "application/vnd.google-apps.drawing"})


def _attachment_get(creds, url: str, params: dict, timeout: int = 180):
    r = AuthorizedSession(_fresh(creds)).get(url, params=params, timeout=timeout)
    if r.status_code == 404:
        raise RuntimeError("첨부 파일을 찾을 수 없습니다 — 링크가 맞는지, 지워지지 않았는지 확인하세요")
    if r.status_code == 403 and b"exportSizeLimitExceeded" in (r.content or b""):
        raise RuntimeError("구글 문서가 너무 커서 PDF 로 내보낼 수 없습니다 (구글 내보내기 10MB 한도)")
    if r.status_code in (401, 403):
        raise RuntimeError("첨부 파일에 접근 권한이 없습니다 — 파일을 공유받았는지 확인하세요")
    if r.status_code != 200:
        raise RuntimeError(f"첨부 파일 읽기 실패 (HTTP {r.status_code})")
    return r


def drive_meta(creds, file_id: str) -> dict:
    """첨부 점검용 — {name, mimeType, size}. 구글 문서류는 size 가 없다."""
    r = _attachment_get(creds, _DRIVE_FILES + file_id,
                        {"fields": "name,mimeType,size", "supportsAllDrives": "true"}, timeout=30)
    return r.json()


def attachment_name(meta: dict) -> str:
    """메일에 붙을 파일명 — 구글 문서류는 `.pdf`. 붙일 수 없는 종류(폴더·설문 등)는 RuntimeError."""
    name, mime = meta.get("name") or "첨부파일", meta.get("mimeType") or ""
    if mime in GOOGLE_EXPORTABLE:
        return name if name.lower().endswith(".pdf") else name + ".pdf"
    if mime.startswith("application/vnd.google-apps."):
        raise RuntimeError(f"첨부할 수 없는 종류입니다 ({name}: {mime.rsplit('.', 1)[-1]}) — 파일 링크를 적으세요")
    return name


def drive_attachment(creds, file_id: str, meta: dict | None = None) -> tuple[str, str, bytes]:
    """(파일명, mime, 내용). 구글 문서류는 files/{id}/export 로 PDF, 그 밖은 alt=media."""
    meta = meta or drive_meta(creds, file_id)
    name = attachment_name(meta)
    if meta.get("mimeType") in GOOGLE_EXPORTABLE:
        r = _attachment_get(creds, _DRIVE_FILES + file_id + "/export", {"mimeType": "application/pdf"})
        return name, "application/pdf", r.content
    r = _attachment_get(creds, _DRIVE_FILES + file_id, {"alt": "media", "supportsAllDrives": "true"})
    return name, meta.get("mimeType") or "application/octet-stream", r.content


def read_template_cache_index(creds, index_id: str) -> dict:
    """'셀라 메일 템플릿 캐시' Apps Script 가 10분마다 쓰는 index.json — {tabs: {탭 ID: {fileId, hash, …}}}."""
    if not index_id:
        raise RuntimeError("템플릿 캐시가 설정되지 않았습니다 (CELLA_MAIL_TEMPLATE_CACHE_INDEX_ID)")
    try:
        index = json.loads(drive_download(creds, index_id).decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as e:
        raise RuntimeError("템플릿 캐시 목록(index.json)이 깨졌습니다 — 10분 뒤 다시 점검하세요") from e
    return index if isinstance(index, dict) else {}


def cached_tab_zip(creds, index: dict, tab_id: str) -> bytes:
    entry = (index.get("tabs") or {}).get(tab_id)
    if not entry or not entry.get("fileId"):
        raise RuntimeError("이 탭이 아직 템플릿 캐시에 없습니다 — 새로 만든 탭이면 최대 10분 뒤 다시 점검하세요")
    data = drive_download(creds, entry["fileId"])
    if data[:2] != b"PK":
        raise RuntimeError("템플릿 캐시 파일이 zip 이 아닙니다 — 10분 뒤 다시 점검하세요")
    return data


def _sheets(creds):
    return build("sheets", "v4", credentials=_fresh(creds), cache_discovery=False)


def sheet_titles(creds, spreadsheet_id: str) -> list[str]:
    meta = _sheets(creds).spreadsheets().get(spreadsheetId=spreadsheet_id,
                                             fields="sheets(properties(title))").execute()
    return [s["properties"]["title"] for s in meta.get("sheets", [])]


def read_sheet(creds, spreadsheet_id: str, title: str) -> list[list[str]]:
    res = _sheets(creds).spreadsheets().values().get(
        spreadsheetId=spreadsheet_id, range=_a1(title), valueRenderOption="FORMATTED_VALUE").execute()
    return res.get("values", [])


def _a1(title: str) -> str:
    return "'" + title.replace("'", "''") + "'"


def _col(i: int) -> str:
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def read_cell(creds, spreadsheet_id: str, title: str, row: int, col: int) -> str:
    res = _sheets(creds).spreadsheets().values().get(
        spreadsheetId=spreadsheet_id, range=f"{_a1(title)}!{_col(col)}{row}").execute()
    vals = res.get("values", [])
    return str(vals[0][0]).strip() if vals and vals[0] else ""


def read_cells(creds, spreadsheet_id: str, title: str, row: int, cols: list[int]) -> dict[int, str]:
    """한 행의 여러 칸을 한 번의 범위 읽기로 — 발송 직전 이메일·전송여부를 같은 시점에 본다."""
    lo, hi = min(cols), max(cols)
    res = _sheets(creds).spreadsheets().values().get(
        spreadsheetId=spreadsheet_id, range=f"{_a1(title)}!{_col(lo)}{row}:{_col(hi)}{row}").execute()
    vals = res.get("values", [])
    line = vals[0] if vals else []
    return {c: (str(line[c - lo]).strip() if c - lo < len(line) else "") for c in cols}


def write_cells(creds, spreadsheet_id: str, title: str, row: int, updates: dict[int, str]) -> None:
    data = [{"range": f"{_a1(title)}!{_col(c)}{row}", "values": [[v]]} for c, v in updates.items()]
    _sheets(creds).spreadsheets().values().batchUpdate(
        spreadsheetId=spreadsheet_id, body={"valueInputOption": "USER_ENTERED", "data": data}).execute()


def send_as_addresses(creds) -> list[dict]:
    gm = build("gmail", "v1", credentials=_fresh(creds), cache_discovery=False)
    items = gm.users().settings().sendAs().list(userId="me").execute().get("sendAs", [])
    out = []
    for s in items:
        if s.get("isPrimary") or s.get("verificationStatus") == "accepted":
            out.append({"email": s["sendAsEmail"], "display": s.get("displayName", ""),
                        "reply_to": s.get("replyToAddress", ""), "default": bool(s.get("isDefault"))})
    return out


def send_raw(creds, raw: bytes) -> str:
    gm = build("gmail", "v1", credentials=_fresh(creds), cache_discovery=False)
    media = MediaIoBaseUpload(io.BytesIO(raw), mimetype="message/rfc822", resumable=True)
    return gm.users().messages().send(userId="me", body={}, media_body=media).execute()["id"]
