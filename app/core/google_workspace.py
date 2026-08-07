"""Google Workspace API wrapper functions.

Stateless functions that accept credentials and call Gmail/Drive/Calendar APIs.
"""

import base64
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import structlog
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

logger = structlog.get_logger(__name__)


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
        List of message dicts with subject, from, date, snippet.
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
            userId="me", id=msg_info["id"], format="metadata",
            metadataHeaders=["Subject", "From", "Date"],
        ).execute()

        headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
        output.append({
            "id": msg_info["id"],
            "subject": headers.get("Subject", "(제목 없음)"),
            "from": headers.get("From", ""),
            "date": headers.get("Date", ""),
            "snippet": msg.get("snippet", ""),
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

    clauses = ["trashed = false"]
    kw = (query or "").strip().replace("'", "\\'")
    if kw:
        clauses.append(f"(name contains '{kw}' or fullText contains '{kw}')")
    if mime_contains:
        clauses.append(f"mimeType contains '{mime_contains}'")
    drive_query = " and ".join(clauses)

    results = service.files().list(
        q=drive_query,
        pageSize=max_results,
        fields="files(id, name, mimeType, modifiedTime, webViewLink)",
        orderBy="modifiedTime desc",
    ).execute()

    files = results.get("files", [])
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
