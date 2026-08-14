"""Google Workspace API wrapper functions.

Stateless functions that accept credentials and call Gmail/Drive/Calendar APIs.
"""

import base64
import html
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import structlog
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

logger = structlog.get_logger(__name__)

# A daily digest can contain ten messages. Keep every message represented in
# the formatter prompt instead of letting the first few long mails crowd out
# the rest; 2,000 characters is enough for a useful per-message summary.
GMAIL_BODY_MAX_CHARS = 2_000


def _gmail_part_headers(part: Dict[str, Any]) -> Dict[str, str]:
    """Return MIME headers with case-insensitive names."""
    return {
        str(header.get("name", "")).lower(): str(header.get("value", ""))
        for header in part.get("headers", [])
    }


def _decode_gmail_part(part: Dict[str, Any]) -> str:
    """Decode a Gmail API MIME part without downloading attachments."""
    data = part.get("body", {}).get("data")
    if not data:
        return ""

    try:
        padded = data + ("=" * (-len(data) % 4))
        raw = base64.urlsafe_b64decode(padded.encode("ascii"))
    except (ValueError, UnicodeEncodeError):
        return ""

    content_type = _gmail_part_headers(part).get("content-type", "")
    charset_match = re.search(r"charset\s*=\s*[\"']?([^;\"'\s]+)", content_type, re.I)
    charsets = [charset_match.group(1)] if charset_match else []
    charsets.extend(["utf-8", "cp949", "latin-1"])
    for charset in charsets:
        try:
            return raw.decode(charset)
        except (LookupError, UnicodeDecodeError):
            continue
    return raw.decode("utf-8", errors="replace")


def _html_to_text(value: str) -> str:
    value = re.sub(r"(?is)<(script|style)\b.*?>.*?</\1>", " ", value)
    value = re.sub(r"(?i)<br\s*/?>", "\n", value)
    value = re.sub(r"(?i)</(?:p|div|li|tr|h[1-6])\s*>", "\n", value)
    value = re.sub(r"(?s)<[^>]+>", " ", value)
    return html.unescape(value)


def _normalize_mail_text(value: str) -> str:
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in value.splitlines()]
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip(" \n\r\t\x00")


def extract_gmail_body(
    payload: Dict[str, Any],
    max_chars: int = GMAIL_BODY_MAX_CHARS,
) -> str:
    """Extract readable body text from a Gmail MIME payload.

    Plain text is preferred over HTML so multipart/alternative messages are
    not duplicated. Parts marked as attachments are intentionally skipped.
    """
    plain_parts: List[str] = []
    html_parts: List[str] = []

    def visit(part: Dict[str, Any]) -> None:
        headers = _gmail_part_headers(part)
        disposition = headers.get("content-disposition", "").lower()
        if part.get("filename") or "attachment" in disposition:
            return

        for child in part.get("parts", []) or []:
            visit(child)

        mime_type = str(part.get("mimeType", "")).lower()
        if mime_type not in {"text/plain", "text/html"}:
            return
        decoded = _decode_gmail_part(part)
        if not decoded:
            return
        if mime_type == "text/plain":
            plain_parts.append(decoded)
        else:
            html_parts.append(_html_to_text(decoded))

    visit(payload or {})
    body = "\n\n".join(plain_parts or html_parts)
    return _normalize_mail_text(body)[:max_chars]


def search_gmail(
    creds: Credentials,
    query: str,
    max_results: int = 10,
) -> List[Dict[str, Any]]:
    """Search Gmail messages.

    Args:
        creds: Valid Google OAuth2 credentials.
        query: Gmail search query (e.g. "from:boss subject:report").
        max_results: Maximum number of messages to return.

    Returns:
        List of message dicts with subject, from, date, snippet, and body.
    """
    service = build("gmail", "v1", credentials=creds, cache_discovery=False)
    results = service.users().messages().list(
        userId="me", q=query, maxResults=max_results
    ).execute()

    messages = results.get("messages", [])
    if not messages:
        return []

    output = []
    for msg_info in messages:
        msg = service.users().messages().get(
            userId="me", id=msg_info["id"], format="full",
        ).execute()

        payload = msg.get("payload", {})
        headers = {h["name"]: h["value"] for h in payload.get("headers", [])}
        output.append({
            "id": msg_info["id"],
            "subject": headers.get("Subject", "(제목 없음)"),
            "from": headers.get("From", ""),
            "date": headers.get("Date", ""),
            "snippet": msg.get("snippet", ""),
            "body": extract_gmail_body(payload),
        })

    return output


def search_drive(
    creds: Credentials,
    query: str,
    max_results: int = 10,
    mime_contains: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Search Google Drive files.

    Args:
        creds: Valid Google OAuth2 credentials.
        query: 핵심 검색 키워드 (빈 문자열이면 이름/본문 조건 없이 최근 파일).
            ⚠️ 사용자 문장 전체를 넣으면 안 된다 — "내 드라이브에서 사진 찾아줘" 를
            name contains 로 검색하면 항상 0건이다 (2026-08-07 실동작 테스트에서 발견).
        max_results: Maximum number of files to return.
        mime_contains: mimeType 부분 일치 필터 (예: "image/", "video/", "application/pdf").

    Returns:
        List of file dicts with name, mimeType, modifiedTime, webViewLink.
    """
    service = build("drive", "v3", credentials=creds, cache_discovery=False)

    def _run(kws: List[str]) -> List[Dict[str, Any]]:
        clauses = ["trashed = false"]
        for k in kws:
            k = k.replace("'", "\\'")
            clauses.append(f"(name contains '{k}' or fullText contains '{k}')")
        if mime_contains:
            clauses.append(f"mimeType contains '{mime_contains}'")
        res = service.files().list(
            q=" and ".join(clauses),
            pageSize=max_results,
            fields="files(id, name, mimeType, modifiedTime, webViewLink)",
            orderBy="modifiedTime desc",
        ).execute()
        return res.get("files", [])

    # ⛔ **낱말을 통째로 한 조건에 넣지 마라.** `name contains '신규 입사자 교안 자료'` 는
    #    그 문구가 통으로 들어간 파일만 찾는다 — 실제 파일명이
    #    "[ICON 교안] 운영본부_부서소개_260805" 였고 0건이 나왔다 (2026-08-14 제보).
    #    낱말마다 조건을 만들어 AND 로 걸고, 0건이면 **핵심어만 남겨 넓힌다.**
    words = [w for w in (query or "").split() if w.strip()]
    if not words:
        files = _run([])
    else:
        files = _run(words[:5])
        if not files and len(words) > 2:
            # 긴 낱말이 더 구체적이고, 한국어는 **뒤에 오는 명사가 머리말**이다
            # ("신규 입사자 교안" → 교안). 길이 우선, 같으면 뒤쪽을 남긴다
            ranked = sorted(enumerate(words), key=lambda kv: (len(kv[1]), kv[0]),
                            reverse=True)
            files = _run([w for _, w in ranked[:2]])
        # ⛔ **한 낱말까지 풀지 않는다.** 실제로 "신규 입사자 교안" 을 한 낱말로 넓혔더니
        #    관련 없는 스프레드시트 4건이 나왔고, 답변은 그걸 찾은 것처럼 보여줬다
        #    (2026-08-14). **0건이라고 말하는 편이 낫다** — 잡음은 답처럼 보여서 더 나쁘다
    return [
        {
            "id": f["id"],
            "name": f.get("name", ""),
            "mimeType": f.get("mimeType", ""),
            "modifiedTime": f.get("modifiedTime", ""),
            "webViewLink": f.get("webViewLink", ""),
        }
        for f in files
    ]


def list_calendar_events(
    creds: Credentials,
    query: Optional[str] = None,
    days_ahead: int = 7,
    days_back: int = 0,
) -> List[Dict[str, Any]]:
    """List Google Calendar events.

    Args:
        creds: Valid Google OAuth2 credentials.
        query: Optional text search query for events.
        days_ahead: Number of days to look ahead.
        days_back: Number of days to look back.

    Returns:
        List of event dicts with summary, start, end, location, htmlLink.
    """
    service = build("calendar", "v3", credentials=creds, cache_discovery=False)

    now = datetime.now(timezone.utc)
    time_min = (now - timedelta(days=days_back)).isoformat()
    time_max = (now + timedelta(days=days_ahead)).isoformat()

    kwargs = {
        "calendarId": "primary",
        "timeMin": time_min,
        "timeMax": time_max,
        "maxResults": 20,
        "singleEvents": True,
        "orderBy": "startTime",
    }
    if query:
        # Google Calendar API q= searches event text (title/description), NOT time.
        # Strip time-only queries that would return 0 results.
        _is_time_only = bool(re.fullmatch(
            r'(?:오전|오후|아침|저녁|점심|새벽)\s*\d{0,2}\s*시?\s*(?:일정|미팅|회의)*'
            r'|\d{1,2}\s*시\s*(?:일정|미팅|회의)*',
            query.strip(),
        ))
        if not _is_time_only:
            kwargs["q"] = query
        else:
            logger.info("calendar_query_time_filter_stripped", original_query=query)

    results = service.events().list(**kwargs).execute()
    events = results.get("items", [])

    return [
        {
            "id": e["id"],
            "summary": e.get("summary", "(제목 없음)"),
            "start": e.get("start", {}).get("dateTime", e.get("start", {}).get("date", "")),
            "end": e.get("end", {}).get("dateTime", e.get("end", {}).get("date", "")),
            "location": e.get("location", ""),
            "htmlLink": e.get("htmlLink", ""),
        }
        for e in events
    ]
