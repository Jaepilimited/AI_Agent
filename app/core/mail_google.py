"""메일 발송용 구글 호출 — 전부 사용자 본인 토큰. WAS 프록시는 HTTPS_PROXY 환경변수로 탄다."""
from __future__ import annotations

import io
import re

import requests
from google.auth.transport.requests import AuthorizedSession, Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

from app.core.google_auth import MAIL_SCOPES


def _fresh(creds):
    if not creds.valid:
        creds.refresh(Request())
    return creds


def granted_scopes(creds) -> set[str]:
    r = requests.get("https://oauth2.googleapis.com/tokeninfo",
                     params={"access_token": _fresh(creds).token}, timeout=20)
    return set((r.json().get("scope") or "").split()) if r.ok else set()


def missing_mail_scopes(creds) -> list[str]:
    have = granted_scopes(creds)
    return [s for s in MAIL_SCOPES if s not in have]


def spreadsheet_id_from(url_or_id: str) -> str:
    m = re.search(r"/spreadsheets/d/([A-Za-z0-9_-]+)", url_or_id)
    return m.group(1) if m else url_or_id.strip()


def export_tab_zip(creds, doc_id: str, tab_id: str) -> bytes:
    r = AuthorizedSession(_fresh(creds)).get(
        f"https://docs.google.com/document/d/{doc_id}/export",
        params={"format": "zip", "tab": tab_id}, timeout=180)
    if r.status_code != 200 or r.content[:2] != b"PK":
        raise RuntimeError(f"탭 내보내기 실패 (HTTP {r.status_code}) — zip 이 아닙니다. 문서 권한을 확인하세요")
    return r.content


def _sheets(creds):
    return build("sheets", "v4", credentials=_fresh(creds), cache_discovery=False)


def sheet_titles(creds, spreadsheet_id: str) -> list[str]:
    meta = _sheets(creds).spreadsheets().get(spreadsheetId=spreadsheet_id,
                                             fields="sheets(properties(title))").execute()
    return [s["properties"]["title"] for s in meta.get("sheets", [])]


def read_sheet(creds, spreadsheet_id: str, title: str) -> list[list[str]]:
    res = _sheets(creds).spreadsheets().values().get(
        spreadsheetId=spreadsheet_id, range=f"'{title}'", valueRenderOption="FORMATTED_VALUE").execute()
    return res.get("values", [])


def _col(i: int) -> str:
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def read_cell(creds, spreadsheet_id: str, title: str, row: int, col: int) -> str:
    res = _sheets(creds).spreadsheets().values().get(
        spreadsheetId=spreadsheet_id, range=f"'{title}'!{_col(col)}{row}").execute()
    vals = res.get("values", [])
    return str(vals[0][0]).strip() if vals and vals[0] else ""


def write_cells(creds, spreadsheet_id: str, title: str, row: int, updates: dict[int, str]) -> None:
    data = [{"range": f"'{title}'!{_col(c)}{row}", "values": [[v]]} for c, v in updates.items()]
    _sheets(creds).spreadsheets().values().batchUpdate(
        spreadsheetId=spreadsheet_id, body={"valueInputOption": "USER_ENTERED", "data": data}).execute()


def send_as_addresses(creds) -> list[dict]:
    gm = build("gmail", "v1", credentials=_fresh(creds), cache_discovery=False)
    items = gm.users().settings().sendAs().list(userId="me").execute().get("sendAs", [])
    out = []
    for s in items:
        if s.get("isPrimary") or s.get("verificationStatus") == "accepted":
            out.append({"email": s["sendAsEmail"], "display": s.get("displayName", ""),
                        "default": bool(s.get("isDefault"))})
    return out


def send_raw(creds, raw: bytes) -> str:
    gm = build("gmail", "v1", credentials=_fresh(creds), cache_discovery=False)
    media = MediaIoBaseUpload(io.BytesIO(raw), mimetype="message/rfc822", resumable=True)
    return gm.users().messages().send(userId="me", body={}, media_body=media).execute()["id"]
