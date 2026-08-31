"""Google Workspace API wrapper functions.

Stateless functions that accept credentials and call Gmail/Drive/Calendar APIs.
"""

import base64
import html
import math
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


#: 월요일·연휴 다음날은 창이 3일 이상이라 20건으로는 앞부분이 통째로 잘린다.
#: 한 건마다 messages.get 이 한 번씩 나가므로 상한은 여전히 필요하다.
GMAIL_DIGEST_HARD_CAP = 40


def list_gmail_digest(
    creds: Credentials,
    start: datetime,
    end: datetime,
    max_results: int = 20,
) -> Dict[str, Any]:
    """Collect bounded Gmail metadata for the personal briefing.

    This intentionally requests Gmail's ``metadata`` format only.  The
    welcome briefing may use headers and the Gmail-provided snippet, but must
    never fetch message bodies or attachments.
    """
    service = build("gmail", "v1", credentials=creds, cache_discovery=False)
    start_epoch = int(start.timestamp())
    end_epoch = math.ceil(end.timestamp())
    query = (
        f"after:{start_epoch - 1} before:{end_epoch} "
        "-in:spam -in:trash -in:drafts -in:sent -from:me"
    )
    bounded_max_results = min(max(1, max_results), GMAIL_DIGEST_HARD_CAP)
    page = service.users().messages().list(
        userId="me", q=query, maxResults=bounded_max_results,
    ).execute()

    items = []
    for ref in page.get("messages", []):
        msg = service.users().messages().get(
            userId="me",
            id=ref["id"],
            format="metadata",
            metadataHeaders=["Subject", "From", "Date"],
        ).execute()
        headers = _gmail_part_headers(msg.get("payload", {}))
        message_id = msg.get("id", ref["id"])
        thread_id = msg.get("threadId", ref.get("threadId", message_id))
        try:
            received_epoch = int(msg.get("internalDate", "0")) / 1000
        except (TypeError, ValueError):
            continue
        if not (start.timestamp() <= received_epoch < end.timestamp()):
            continue
        items.append({
            "id": message_id,
            "thread_id": thread_id,
            "subject": headers.get("subject", "(제목 없음)"),
            "from": headers.get("from", ""),
            "received_at": datetime.fromtimestamp(
                received_epoch,
                timezone.utc,
            ).isoformat(),
            "unread": "UNREAD" in msg.get("labelIds", []),
            "snippet": msg.get("snippet", "")[:500],
            "url": f"https://mail.google.com/mail/u/0/#all/{message_id}",
        })

    return {"items": items, "truncated": bool(page.get("nextPageToken"))}


def list_calendar_window(
    creds: Credentials,
    start: datetime,
    end: datetime,
    max_results: int = 50,
) -> Dict[str, Any]:
    """Collect primary-calendar events in the exact supplied time window."""
    service = build("calendar", "v3", credentials=creds, cache_discovery=False)
    page = service.events().list(
        calendarId="primary",
        timeMin=start.astimezone(timezone.utc).isoformat(),
        timeMax=end.astimezone(timezone.utc).isoformat(),
        maxResults=max_results,
        singleEvents=True,
        orderBy="startTime",
    ).execute()

    return {
        "items": [
            {
                "id": event["id"],
                "summary": event.get("summary", "(제목 없음)"),
                "start": event.get("start", {}).get(
                    "dateTime", event.get("start", {}).get("date", ""),
                ),
                "end": event.get("end", {}).get(
                    "dateTime", event.get("end", {}).get("date", ""),
                ),
                "location": event.get("location", ""),
                "htmlLink": event.get("htmlLink", ""),
                "attendees": _event_attendees(event),
                "organizer": (event.get("organizer") or {}).get(
                    "displayName", (event.get("organizer") or {}).get("email", ""),
                ),
                "conference_url": _event_conference_url(event),
                "declined": _self_declined(event),
                # 사전 준비 문장을 만들 때만 쓰는 임시값이다. 저장 전에 벗겨진다.
                "description": (event.get("description") or "")[:600],
            }
            for event in page.get("items", [])
        ],
        "truncated": bool(page.get("nextPageToken")),
    }


def _event_attendees(event: Dict[str, Any]) -> List[str]:
    """참석자 표시 이름만. 사람이 읽을 목록이라 리소스(회의실)와 본인은 뺀다."""
    names = []
    for person in event.get("attendees", []) or []:
        if person.get("resource") or person.get("self"):
            continue
        label = person.get("displayName") or person.get("email") or ""
        label = label.split("@")[0] if "@" in label and not person.get("displayName") else label
        if label and label not in names:
            names.append(label)
        if len(names) >= 20:
            break
    return names


def _event_conference_url(event: Dict[str, Any]) -> str:
    """온라인 회의 링크. Meet 이 없으면 첫 video entry point 를 쓴다."""
    direct = event.get("hangoutLink") or ""
    if direct:
        return direct
    for entry in (event.get("conferenceData") or {}).get("entryPoints", []) or []:
        if entry.get("entryPointType") == "video" and entry.get("uri", "").startswith("https://"):
            return entry["uri"]
    return ""


def _self_declined(event: Dict[str, Any]) -> bool:
    for person in event.get("attendees", []) or []:
        if person.get("self"):
            return person.get("responseStatus") == "declined"
    return False


#: 구글이 관리하는 대한민국 공휴일 캘린더. 손으로 적은 표가 낡는 것을 막는 유일한 이유다.
KOREA_HOLIDAY_CALENDAR_ID = "ko.south_korea#holiday@group.v.calendar.google.com"


def list_korea_holidays(
    creds: Credentials,
    start: datetime,
    end: datetime,
) -> List[str]:
    """[start, end) 안의 한국 공휴일 날짜(YYYY-MM-DD).

    ⛔ 실패를 삼키지 않는다 — 호출부가 '확인 실패' 를 브리핑에 적어야 하므로
    예외를 그대로 올린다.
    """
    service = build("calendar", "v3", credentials=creds, cache_discovery=False)
    page = service.events().list(
        calendarId=KOREA_HOLIDAY_CALENDAR_ID,
        timeMin=start.astimezone(timezone.utc).isoformat(),
        timeMax=end.astimezone(timezone.utc).isoformat(),
        maxResults=60,
        singleEvents=True,
        orderBy="startTime",
    ).execute()
    days = []
    for event in page.get("items", []):
        value = (event.get("start") or {}).get("date", "")
        if len(value) == 10 and value not in days:
            days.append(value)
    return days


def search_drive(
    creds: Credentials,
    query: str,
    max_results: int = 10,
    mime_contains: Optional[str] = None,
    exact_name: Optional[str] = None,
    widen: bool = True,
) -> List[Dict[str, Any]]:
    """Search Google Drive files.

    Args:
        creds: Valid Google OAuth2 credentials.
        query: 핵심 검색 키워드 (빈 문자열이면 이름/본문 조건 없이 최근 파일).
            ⚠️ 사용자 문장 전체를 넣으면 안 된다 — "내 드라이브에서 사진 찾아줘" 를
            name contains 로 검색하면 항상 0건이다 (2026-08-07 실동작 테스트에서 발견).
        max_results: Maximum number of files to return.
        mime_contains: mimeType 부분 일치 필터 (예: "image/", "video/", "application/pdf").
        exact_name: 주어지면 그 문자열이 파일명에 그대로 든 것만 남긴다 (근접 롯트 오검출 방지).
        widen: False 면 낱말을 줄여 재조회하는 내부 완화(fallback)를 하지 않는다.
            ⛔ 롯트 조회처럼 **넓히면 안 되는** 호출에서 True(기본값)로 두면 관련 없는
            파일이 조용히 "찾음" 으로 나간다.

    Returns:
        List of file dicts with name, mimeType, modifiedTime, webViewLink, size, parentId.
    """
    service = build("drive", "v3", credentials=creds, cache_discovery=False)

    def _run(kws: List[str]) -> List[Dict[str, Any]]:
        clauses = ["trashed = false"]
        for k in kws:
            k = k.replace("'", "\\'")
            clauses.append(f"(name contains '{k}' or fullText contains '{k}')")
        if mime_contains:
            clauses.append(f"mimeType contains '{mime_contains}'")
        # ⚠️ 검색어가 있으면 **정렬을 지정하지 않는다** — Drive 가 관련도 순으로 준다.
        #    `modifiedTime desc` 로 고정하면 딱 맞는 파일이 최근 파일에 밀려 상위 N 밖으로
        #    나간다. 키워드가 없을 때(최근 파일 보기)만 수정일 순이 맞다.
        params = dict(
            q=" and ".join(clauses), pageSize=max_results,
            fields="files(id, name, mimeType, modifiedTime, webViewLink, size, parents)",
            # ⛔ 공유드라이브를 빼면 COA·MSDS 가 통째로 안 보인다. Drive 는 기본값이
            #    'user' 라 공유드라이브 항목을 조용히 제외한다 — 에러가 아니라 0건이다
            corpora="allDrives",
            includeItemsFromAllDrives=True,
            supportsAllDrives=True,
        )
        if not kws:
            params["orderBy"] = "modifiedTime desc"
        # ⚠️ 병렬 조회(최대 8개 동시)라 Drive 가 간헐적으로 429/5xx 를 준다 — 사람은
        #    이걸 "진짜 없음" 과 구분할 수 없다. googleapiclient 의 내장 지수 백오프로
        #    재시도한다 (직접 루프를 짜지 않는다)
        res = service.files().list(**params).execute(num_retries=3)
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
        if widen and not files and len(words) > 2:
            # 긴 낱말이 더 구체적이고, 한국어는 **뒤에 오는 명사가 머리말**이다
            # ("신규 입사자 교안" → 교안). 길이 우선, 같으면 뒤쪽을 남긴다
            ranked = sorted(enumerate(words), key=lambda kv: (len(kv[1]), kv[0]),
                            reverse=True)
            files = _run([w for _, w in ranked[:2]])
        # ⛔ **한 낱말까지 풀지 않는다.** 실제로 "신규 입사자 교안" 을 한 낱말로 넓혔더니
        #    관련 없는 스프레드시트 4건이 나왔고, 답변은 그걸 찾은 것처럼 보여줬다
        #    (2026-08-14). **0건이라고 말하는 편이 낫다** — 잡음은 답처럼 보여서 더 나쁘다
    # ⛔ Drive 의 `contains` 는 토큰 앞부분 매칭이라 요청하지 않은 파일이 섞일 수 있다.
    #    롯트 조회는 **넓히면 안 되는** 경로다 — 문자열이 그대로 든 것만 남긴다
    if exact_name:
        # ⚠️ 대소문자는 가리지 않는다 — Drive 의 contains 가 무시하므로 조회는
        #    파일을 찾아오는데 여기서 버리면 에러가 아니라 **0건**이 난다
        #    (롯트를 소문자로 적은 목록이 통째로 '없음' 이 됐다). 걸러내는
        #    기준은 그대로다: 문자열이 이름에 통으로 들어 있어야 한다
        folded = exact_name.casefold()
        files = [f for f in files if folded in f.get("name", "").casefold()]
    return [
        {
            "id": f["id"],
            "name": f.get("name", ""),
            "mimeType": f.get("mimeType", ""),
            "modifiedTime": f.get("modifiedTime", ""),
            "webViewLink": f.get("webViewLink", ""),
            "size": int(f.get("size") or 0),
            "parentId": (f.get("parents") or [None])[0],
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
