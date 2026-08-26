"""Read-only, cached aggregation for a user's login work briefing.

Only compact Gmail metadata and Calendar event metadata cross this boundary.
Raw snippets are used transiently for the optional mail summary and are stripped
before a result is returned or persisted.
"""

from __future__ import annotations

import asyncio
import hashlib
from datetime import date, datetime, time, timedelta
from email.header import decode_header
from email.utils import parseaddr
from typing import Any, Callable
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import structlog

from app.core import briefing
from app.core import fx_rates
from app.core import jandi_briefing
from app.core import work_briefing
from app.core import workday
from app.core.google_auth import CredentialLoadOutcome, GoogleAuthManager
from app.core.google_workspace import (
    list_calendar_window,
    list_gmail_digest,
    list_korea_holidays,
)
from app.core import personal_briefing_store as store
from app.db.mariadb import fetch_all
from app.db.models import User

logger = structlog.get_logger()

SEOUL = ZoneInfo("Asia/Seoul")
CACHE_TTL = timedelta(minutes=10)
GOOGLE_TIMEOUT_SECONDS = 10.0
# 월요일·연휴 다음날은 메일 40건을 건별로 가져온다 (messages.get 이 한 건에 한 번).
# ⚠️ 10초를 그대로 쓰면 그날만 메일 섹션이 통째로 google_timeout 이 된다 —
#    창을 넓히면서 이 상한을 같이 올리지 않으면 조용히 실패한다.
GOOGLE_TIMEOUT_MULTI_DAY_SECONDS = 20.0
# ⚠️ 메일 18건·일정 16건이면 5초대가 나온다 — 8초 상한은 경계선이라 부하가 조금만
#    있어도 넘겼다 (2026-08-26 실측). 넘으면 문장이 통째로 빠진 문서가 저장된다.
SUMMARY_TIMEOUT_SECONDS = 20.0
# 월요일 창은 3일치라 Gmail 수집이 길어진다. 브리핑 문장 생성도 이 예산 안에서 끝나야 한다.
REFRESH_TIMEOUT_SECONDS = 25.0
HOLIDAY_TIMEOUT_SECONDS = 6.0
_ALLOWED_LINKS = {"mail.google.com", "calendar.google.com", "meet.google.com"}

_locks: dict[int, asyncio.Lock] = {}
_auth_manager = GoogleAuthManager()


def get_user_refresh_lock(user_id: int) -> asyncio.Lock:
    """Return the single process-local lock for a user's refresh/auth mutations."""

    return _locks.setdefault(int(user_id), asyncio.Lock())


def briefing_window(now: datetime | None = None) -> tuple[date, datetime, datetime]:
    """Return the KST day and the exact [today, today+7) calendar window."""

    current = (now or datetime.now(SEOUL)).astimezone(SEOUL)
    day = current.date()
    start = datetime.combine(day, time.min, tzinfo=SEOUL)
    return day, start, start + timedelta(days=7)


def _parse_event_time(value: str) -> datetime:
    if len(value) == 10:
        return datetime.fromisoformat(value).replace(tzinfo=SEOUL)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.replace(tzinfo=SEOUL) if parsed.tzinfo is None else parsed.astimezone(SEOUL)


def _event_has_ended(end_value: str, now: datetime) -> bool:
    return _parse_event_time(end_value) <= now.astimezone(SEOUL)


def _safe_google_link(value: str) -> str:
    """Permit only the exact external domains the welcome UI is allowed to link."""

    try:
        parsed = urlparse(value)
    except (TypeError, ValueError):
        return ""
    if parsed.scheme != "https":
        return ""
    if parsed.hostname in _ALLOWED_LINKS:
        return value
    if parsed.hostname == "www.google.com" and parsed.path.startswith("/calendar/"):
        return value
    return ""


def _normalize_calendar(raw: dict[str, Any], now: datetime) -> dict[str, Any]:
    items = []
    for event in raw.get("items", []):
        start_value = str(event.get("start", ""))
        end_value = str(event.get("end", ""))
        if not start_value or not end_value or not event.get("id"):
            continue
        items.append({
            "id": str(event["id"]),
            "title": str(event.get("summary", "(제목 없음)")),
            "start": start_value,
            "end": end_value,
            "all_day": len(start_value) == 10,
            "location": str(event.get("location", "")),
            "url": _safe_google_link(str(event.get("htmlLink", ""))),
            "ended": _event_has_ended(end_value, now),
            "attendees": [str(name) for name in (event.get("attendees") or [])][:20],
            "organizer": str(event.get("organizer", "")),
            "conference_url": _safe_google_link(str(event.get("conference_url", ""))),
            "declined": bool(event.get("declined")),
            # 사전 준비 문장을 만들 때만 쓰는 임시값. 저장 전에 _strip_event_notes 가 벗긴다.
            "description": str(event.get("description", ""))[:600],
        })
    return {
        "status": "ready" if items else "empty",
        "items": items,
        "truncated": bool(raw.get("truncated", False)),
        "error_code": "",
    }


def _sender_display(value: str) -> str:
    """From 헤더에서 사람이 읽을 이름만 남긴다.

    원본은 `"Fred from Fireflies.ai" <fred@fireflies.ai>` 처럼 온다 — 그대로 두면
    브리핑 한 줄의 절반을 주소가 먹는다. 이름이 없으면 주소의 앞부분을 쓴다.
    """

    raw = str(value or "").strip()
    if not raw:
        return ""
    name, address = parseaddr(raw)
    if name:
        try:
            name = "".join(
                part.decode(charset or "utf-8", "replace") if isinstance(part, bytes) else part
                for part, charset in decode_header(name)
            )
        except (UnicodeDecodeError, LookupError, ValueError):
            pass
        name = name.strip().strip('"').strip()
    if name:
        return name[:60]
    return (address.split("@")[0] if "@" in address else address or raw)[:60]


def _normalize_mail(raw: dict[str, Any]) -> dict[str, Any]:
    items = []
    for item in raw.get("items", []):
        message_id = str(item.get("id", ""))
        if not message_id:
            continue
        items.append({
            "id": message_id,
            "thread_id": str(item.get("thread_id", "")),
            "subject": str(item.get("subject", "(제목 없음)")),
            "from_display": _sender_display(str(item.get("from", item.get("from_display", "")))),
            "received_at": str(item.get("received_at", "")),
            "unread": bool(item.get("unread", False)),
            "snippet": str(item.get("snippet", "")),
            "url": _safe_google_link(str(item.get("url", ""))),
        })
    truncated = bool(raw.get("truncated"))
    return {
        "status": "ready" if items else "empty",
        "count_label": f"{len(items)}건 이상" if truncated else f"{len(items)}건",
        "unread": sum(1 for item in items if item["unread"]),
        "summary": "",
        "action_candidates": [],
        "items": items,
        "truncated": truncated,
        "error_code": "",
    }


def _strip_event_notes(calendar: dict[str, Any]) -> dict[str, Any]:
    """일정 설명은 사전 준비 문장을 만드는 동안만 살아 있다 — 메일 미리보기와 같은 취급."""

    result = dict(calendar)
    result["items"] = [
        {key: value for key, value in item.items() if key != "description"}
        for item in result.get("items", [])
    ]
    return result


def _strip_mail_snippets(mail: dict[str, Any]) -> dict[str, Any]:
    result = dict(mail)
    result["items"] = [
        {key: value for key, value in item.items() if key != "snippet"}
        for item in result.get("items", [])
    ]
    return result


def build_document(
    calendar: dict[str, Any],
    mail: dict[str, Any],
    window_meta: dict[str, Any],
    day: date,
    now: datetime,
) -> dict[str, Any]:
    """LLM 호출 1회 + 결정적 검증. 이 함수가 브리핑 문장의 **단일 소스**다."""

    events = calendar.get("items", []) or []
    mails = mail.get("items", []) or []
    raw = work_briefing.generate(
        [event for event in events if work_briefing.is_today(event, day)], mails, day,
    )
    return work_briefing.compose(
        day=day, now=now, events=events, mails=mails, window=window_meta, raw=raw,
    )


async def _build_document_async(
    calendar: dict[str, Any], mail: dict[str, Any], window_meta: dict[str, Any],
    day: date, now: datetime,
) -> dict[str, Any]:
    return await asyncio.wait_for(
        asyncio.to_thread(build_document, calendar, mail, window_meta, day, now),
        timeout=SUMMARY_TIMEOUT_SECONDS,
    )


def _document_only_facts(
    calendar: dict[str, Any], mail: dict[str, Any],
    window_meta: dict[str, Any], day: date, now: datetime,
) -> dict[str, Any]:
    """LLM 이 죽어도 문서는 나온다 — 일정·메일 목록은 **조회 결과라 LLM 과 무관하다.**

    ⛔ 예전엔 `events=[]` 로 만들어 **일정이 통째로 사라졌다** (2026-08-26 이해인 님
       제보로 발견). 없어진 것이 아니라 못 실은 것인데 화면에는 "일정 없음" 으로 보였다.
    """

    return work_briefing.compose(
        day=day, now=now, events=calendar.get("items", []) or [],
        mails=mail.get("items", []) or [], window=window_meta, raw={},
    )


def _apply_document(
    calendar: dict[str, Any], mail: dict[str, Any], document: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """카드 요약을 문서에서 **파생**시킨다.

    ⛔ 같은 메일을 두고 카드와 문서가 각자 문장을 만들면 한 화면에서 서로 다른 말을 한다
       (direct 프롬프트가 두 벌이라 경로마다 답이 갈렸던 것과 같은 부류). 사본을 두지 않는다.
    """

    result = _strip_mail_snippets(mail)
    result["summary"] = str(document.get("mail_summary", ""))
    result["action_candidates"] = [
        {"message_id": row["id"], "reason": row.get("request", "")}
        for row in document.get("mail", []) or []
        if row.get("request")
    ][:3]
    if document.get("status") == "error":
        result["error_code"] = result.get("error_code") or "summary_failed"
    return _strip_event_notes(calendar), result


def build_priorities(
    calendar: dict[str, Any], mail: dict[str, Any], business: dict[str, Any], now: datetime,
) -> list[dict[str, Any]]:
    """Compose at most one priority per source, never inventing source IDs."""

    priorities = []
    upcoming: list[tuple[datetime, dict[str, Any]]] = []
    for item in calendar.get("items", []):
        if item.get("ended") or not item.get("id") or not item.get("title"):
            continue
        try:
            upcoming.append((_parse_event_time(str(item["start"])), item))
        except (KeyError, TypeError, ValueError):
            continue
    upcoming.sort(key=lambda entry: entry[0])
    if upcoming and upcoming[0][0] <= now.astimezone(SEOUL) + timedelta(hours=24):
        event = upcoming[0][1]
        priorities.append({
            "source": "calendar", "source_id": event["id"], "title": event["title"],
            "reason": "24시간 안에 시작", "url": event.get("url", ""),
        })

    mail_by_id = {item.get("id"): item for item in mail.get("items", [])}
    if mail.get("action_candidates") and isinstance(mail["action_candidates"][0], dict):
        candidate = mail["action_candidates"][0]
        message = mail_by_id.get(candidate.get("message_id"))
        if message:
            priorities.append({
                "source": "mail", "source_id": message["id"], "title": message["subject"],
                "reason": candidate.get("reason", ""), "url": message.get("url", ""),
            })

    item = business.get("item") if business.get("status") == "ready" else None
    if item:
        priorities.append({
            "source": "business", "source_id": str(item["id"]), "title": item["title"],
            "reason": "최근 업무 지표 변화", "url": "", "follow_up": item.get("follow_up", ""),
        })
    return priorities[:3]


def _account_hash(value: str) -> str:
    return hashlib.sha256((value or "").strip().lower().encode()).hexdigest()


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=SEOUL) if value.tzinfo is None else value.astimezone(SEOUL)


def _needs_refresh(generated_at: datetime | None, now: datetime) -> bool:
    return generated_at is None or _aware(generated_at) <= now.astimezone(SEOUL) - CACHE_TTL


def _business_for_user(user_id: int) -> dict[str, Any]:
    if briefing.is_opted_out(user_id):
        return {"status": "disabled", "item": None}
    # ⛔ `for_user()` 는 알림함용이라 조용한 갱신분이 빠진다 — 며칠 전 지표가 붙는다.
    row = briefing.latest_for_user(user_id)
    if not row:
        return {"status": "empty", "item": None}
    # ⛔ 지표는 **저장된 알림이 아니라 최신 집계**에서 만든다 — 알릴 만한 변화가 없는
    #    날엔 알림 행이 없어 며칠 전 수치가 붙는다 (2026-08-26 사용자 제보).
    #    저장 행에서는 **관심 축(scope)만** 가져온다. 그건 사람마다 고정된 값이다.
    scope = row.get("scope", "") or "전사"
    sales_snapshot = briefing.latest_sales_snapshot()
    marketing_snapshot = briefing.latest_marketing_snapshot()

    sales = briefing.sales_item(scope, sales_snapshot) or {
        "for_date": str(row["for_date"]), "title": row.get("title", ""),
        "body": briefing.sales_body(row.get("body", "")),
        "follow_up": row.get("follow_up", ""),
    }
    sales = {"kind": "sales", "id": str(row["id"]), **sales}

    items = [sales]
    for kind, builder in (("marketing", briefing.marketing_item),
                          ("roas", briefing.roas_item)):
        made = builder(scope, marketing_snapshot)
        if made:
            items.append({"kind": kind, "id": f"{kind}-{row['id']}", **made})
    # `item` 은 우선순위·옵트아웃 판정이 쓰는 기존 키다 — 매출 쪽을 그대로 둔다.
    return {"status": "ready", "item": sales, "items": items}


def _safe_fx(day: date) -> dict[str, Any]:
    """환율은 있으면 좋은 값이다 — 없거나 터져도 브리핑 전체를 막지 않는다."""

    try:
        return fx_rates.latest(day)
    except Exception as exc:
        logger.warning("fx_unavailable", error_type=type(exc).__name__)
        return {"status": "error", "for_date": "", "stale_days": 0, "items": []}


def _safe_business_for_user(user_id: int) -> dict[str, Any]:
    """Business briefing failures must not hide otherwise safe Google cards."""

    try:
        return _business_for_user(user_id)
    except Exception:
        return {"status": "error", "item": None}


def _merge_failed_section(previous: dict[str, Any] | None, error_code: str) -> dict[str, Any]:
    if previous:
        result = dict(previous)
        result.update(status="stale", error_code=error_code)
        return result
    return {"status": "error", "items": [], "truncated": False, "error_code": error_code}


def _blank_document(day: date, status: str) -> dict[str, Any]:
    """조회할 것이 없을 때도 문서 모양은 같아야 한다 — 프론트가 분기하지 않도록."""

    return {
        "status": status, "for_date": str(day), "weekday": "월화수목금토일"[day.weekday()],
        "window": {}, "mail_summary": "", "meetings": [], "mail": [], "actions": [],
        "deadlines": [], "mail_total": 0, "mail_unread": 0, "dropped": 0, "urgent": 0,
        "markdown": "",
    }


def _empty_sections(status: str, error_code: str) -> tuple[dict[str, Any], dict[str, Any]]:
    return (
        {"status": status, "items": [], "truncated": False, "error_code": error_code},
        {"status": status, "count_label": "0건", "unread": 0, "summary": "",
         "action_candidates": [], "items": [], "truncated": False, "error_code": error_code},
    )


def _snapshot_for_account(snapshot: dict[str, Any] | None, account: str, user_email: str) -> dict[str, Any] | None:
    """Return a snapshot only when it belongs to the current Google account."""

    if not snapshot:
        return None
    expected_hash = _account_hash(account or user_email)
    return snapshot if snapshot.get("google_account_hash") == expected_hash else None


def _visible_priorities(priorities: list[dict[str, Any]], business: dict[str, Any]) -> list[dict[str, Any]]:
    """An opt-out applies to every business-derived presentation, including cache."""

    item = business.get("item") if business.get("status") == "ready" else None
    current_id = str(item.get("id", "")) if isinstance(item, dict) else ""
    return [
        priority
        for priority in priorities
        if priority.get("source") != "business"
        or (current_id and str(priority.get("source_id", "")) == current_id)
    ]


def get_cached_for_user(user: User, now: datetime | None = None) -> dict[str, Any]:
    current = (now or datetime.now(SEOUL)).astimezone(SEOUL)
    day, _start, _end = briefing_window(current)
    connected = _auth_manager.has_credentials(user.email)
    account = _auth_manager.get_stored_google_email(user.email) if connected else ""
    snapshot = store.get_snapshot(user.id, day)
    if not connected:
        snapshot = None
    else:
        snapshot = _snapshot_for_account(snapshot, account, user.email)
    if snapshot:
        calendar, mail, priorities = snapshot["calendar"], snapshot["mail"], snapshot["priorities"]
        document = snapshot.get("document") or _blank_document(day, "empty")
        generated = snapshot["generated_at"]
    else:
        calendar, mail = _empty_sections("disconnected" if not connected else "empty", "oauth_missing" if not connected else "")
        document = _blank_document(day, "disconnected" if not connected else "empty")
        priorities, generated = [], None
    business = _safe_business_for_user(user.id)
    return {
        "enabled": True, "for_date": str(day), "timezone": "Asia/Seoul",
        "generated_at": _aware(generated).isoformat() if generated else "",
        "needs_refresh": connected and _needs_refresh(generated, current),
        "google": {"connected": connected, "account": account},
        "priorities": _visible_priorities(priorities, business), "calendar": calendar, "mail": mail,
        "business": business, "document": document, "fx": _safe_fx(day),
    }


async def _collect_with_timeout(
    fn: Callable[..., Any], *args: Any, timeout: float = GOOGLE_TIMEOUT_SECONDS,
) -> Any:
    return await asyncio.wait_for(asyncio.to_thread(fn, *args), timeout=timeout)


def _error_code(exc: BaseException) -> str:
    text = str(exc).lower()
    if "quota" in text or "429" in text:
        return "google_quota"
    if "refresh" in text or "invalid_grant" in text:
        return "oauth_expired"
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
        return "google_timeout"
    return "google_error"


#: 공휴일은 하루에 한 번만 물어보면 된다. (연·월 키, 값은 날짜 집합)
_holiday_cache: dict[str, frozenset[date]] = {}


def _holidays_for(creds: Any, current: datetime) -> tuple[frozenset[date], str]:
    """구글 '대한민국 공휴일' 캘린더 실측. 못 읽으면 주말만 보고 그 사실을 돌려준다."""

    key = current.strftime("%Y-%m")
    if key in _holiday_cache:
        return _holiday_cache[key], "calendar"
    window_start = datetime.combine(
        current.date() - timedelta(days=workday.MAX_LOOKBACK_DAYS + 2), time.min, tzinfo=SEOUL,
    )
    window_end = datetime.combine(current.date() + timedelta(days=2), time.min, tzinfo=SEOUL)
    try:
        days = list_korea_holidays(creds, window_start, window_end)
    except Exception as exc:
        # ⛔ 조용히 넘기지 않는다. 브리핑 본문에도 "공휴일 확인 실패" 가 찍힌다.
        logger.warning("briefing_holidays_unavailable", error_type=type(exc).__name__)
        return frozenset(), "fallback"
    parsed = frozenset(date.fromisoformat(value) for value in days)
    _holiday_cache.clear()
    _holiday_cache[key] = parsed
    return parsed, "calendar"


async def _mail_window_for(creds: Any, current: datetime) -> tuple[datetime, datetime, dict[str, Any]]:
    try:
        holidays, source = await asyncio.wait_for(
            asyncio.to_thread(_holidays_for, creds, current), timeout=HOLIDAY_TIMEOUT_SECONDS,
        )
    except Exception:
        holidays, source = frozenset(), "fallback"
    return workday.mail_window(current, holidays, source)


async def _refresh_sections(
    creds: Any, start: datetime, end: datetime, current: datetime,
    mail_start: datetime | None = None,
) -> tuple[Any, Any]:
    """Run Google calls concurrently; the briefing document is the only follow-up."""

    since = mail_start or start
    # 창이 하루를 넘으면(월요일·연휴 다음날) 20건으로는 앞부분이 통째로 잘린다.
    multi_day = (current - since) > timedelta(days=1)
    calendar_raw, mail_raw = await asyncio.gather(
        _collect_with_timeout(list_calendar_window, creds, start, end, 50),
        _collect_with_timeout(
            list_gmail_digest, creds, since, current, 40 if multi_day else 20,
            timeout=GOOGLE_TIMEOUT_MULTI_DAY_SECONDS if multi_day else GOOGLE_TIMEOUT_SECONDS,
        ),
        return_exceptions=True,
    )
    return calendar_raw, mail_raw


async def refresh_for_user(user: User, now: datetime | None = None, force: bool = False) -> dict[str, Any]:
    current = (now or datetime.now(SEOUL)).astimezone(SEOUL)
    lock = get_user_refresh_lock(user.id)
    async with lock:
        cached = await asyncio.to_thread(get_cached_for_user, user, current)
        if not force and not cached["needs_refresh"]:
            return cached
        credential_outcome: CredentialLoadOutcome = await asyncio.to_thread(
            _auth_manager.load_credentials, user.email,
        )
        creds = credential_outcome.credentials
        if creds is None and not credential_outcome.definitive_disconnect:
            same_day = cached.get("for_date") == str(current.date())
            error_code = credential_outcome.error_code or "google_error"
            calendar = _merge_failed_section(
                cached.get("calendar") if same_day else None, error_code,
            )
            mail = _merge_failed_section(
                cached.get("mail") if same_day else None, error_code,
            )
            mail_defaults = _empty_sections("error", error_code)[1]
            for key, value in mail_defaults.items():
                mail.setdefault(key, value)
            cached.update(
                calendar=calendar,
                mail=mail,
                priorities=cached.get("priorities", []) if same_day else [],
                document=cached.get("document") or _blank_document(current.date(), "error"),
                needs_refresh=False,
            )
            return cached
        if creds is None:
            await asyncio.to_thread(_auth_manager.revoke_credentials, user.email)
            await asyncio.to_thread(store.delete_for_user, user.id)
            error_code = credential_outcome.error_code or "oauth_expired"
            calendar, mail = _empty_sections("disconnected", error_code)
            cached.update(
                calendar=calendar,
                mail=mail,
                priorities=[],
                document=_blank_document(current.date(), "disconnected"),
                generated_at="",
                needs_refresh=False,
            )
            # ⚠️ 지표·환율은 구글과 무관하다 — 연결이 끊겨도 화면에 남아야 한다.
            cached["business"] = _safe_business_for_user(user.id)
            cached["fx"] = _safe_fx(current.date())
            cached["google"] = {"connected": False, "account": ""}
            return cached

        day, start, end = briefing_window(current)
        credential_identity = await asyncio.to_thread(
            _auth_manager.get_credential_identity, user.email,
        )
        account = await asyncio.to_thread(_auth_manager.get_stored_google_email, user.email)
        account = account or user.email
        old = await asyncio.to_thread(store.get_snapshot, user.id, day)
        old = _snapshot_for_account(old, account, user.email)
        previous_calendar = old.get("calendar") if old else None
        previous_mail = old.get("mail") if old else None
        mail_start, _mail_end, window_meta = await _mail_window_for(creds, current)
        try:
            calendar_raw, mail_raw = await asyncio.wait_for(
                _refresh_sections(creds, start, end, current, mail_start),
                timeout=REFRESH_TIMEOUT_SECONDS,
            )
        except (TimeoutError, asyncio.TimeoutError):
            calendar_raw = asyncio.TimeoutError()
            mail_raw = asyncio.TimeoutError()

        if isinstance(calendar_raw, BaseException):
            calendar = _merge_failed_section(previous_calendar, _error_code(calendar_raw))
        else:
            try:
                calendar = _normalize_calendar(calendar_raw, current)
            except Exception:
                calendar = _merge_failed_section(previous_calendar, "google_error")
        if isinstance(mail_raw, BaseException):
            mail = _merge_failed_section(previous_mail, _error_code(mail_raw))
            defaults = _empty_sections("error", "")[1]
            for key, value in defaults.items():
                mail.setdefault(key, value)
        else:
            try:
                mail = _normalize_mail(mail_raw)
            except Exception:
                mail = _merge_failed_section(previous_mail, "google_error")
                defaults = _empty_sections("error", "")[1]
                for key, value in defaults.items():
                    mail.setdefault(key, value)

        # 문서 생성이 실패해도 조회 결과는 살린다 — 일정·메일 목록은 LLM 과 무관하다.
        try:
            document = await _build_document_async(calendar, mail, window_meta, day, current)
        except Exception as exc:
            logger.warning("briefing_document_failed", error_type=type(exc).__name__)
            try:
                document = _document_only_facts(calendar, mail, window_meta, day, current)
            except Exception:
                document = _blank_document(day, "error")
            document["status"] = "error"
        calendar, mail = _apply_document(calendar, mail, document)

        business = await asyncio.to_thread(_safe_business_for_user, user.id)
        try:
            priorities = build_priorities(calendar, mail, business, current)
        except (KeyError, TypeError, ValueError):
            priorities = []
        priorities = _visible_priorities(priorities, business)
        final_identity = await asyncio.to_thread(
            _auth_manager.get_credential_identity, user.email,
        )
        final_account = await asyncio.to_thread(
            _auth_manager.get_stored_google_email, user.email,
        )
        final_account = final_account or user.email
        if (
            not credential_identity
            or credential_identity != final_identity
            or account.strip().casefold() != final_account.strip().casefold()
        ):
            await asyncio.to_thread(store.delete_for_user, user.id)
            calendar, mail = _empty_sections("disconnected", "oauth_expired")
            return {
                "enabled": True,
                "for_date": str(day),
                "timezone": "Asia/Seoul",
                "generated_at": "",
                "needs_refresh": False,
                "google": {"connected": False, "account": ""},
                "priorities": [],
                "calendar": calendar,
                "mail": mail,
                "business": business,
                "document": _blank_document(day, "disconnected"),
            }
        generated_at = current.replace(tzinfo=None)
        await asyncio.to_thread(
            store.put_snapshot, user.id, day, _account_hash(account),
            calendar, mail, priorities, generated_at, document,
        )
        return {
            "enabled": True, "for_date": str(day), "timezone": "Asia/Seoul",
            "generated_at": current.isoformat(), "needs_refresh": False,
            "google": {"connected": True, "account": account},
            "priorities": priorities, "calendar": calendar, "mail": mail,
            "business": business, "document": document, "fx": _safe_fx(day),
        }


async def run_morning_precompute(now: datetime | None = None) -> dict[str, int]:
    """아침 브리핑을 미리 만들고, 잔디를 등록한 사람 몫은 발송 대기열에 넣는다.

    ⛔ 발송은 여기서 하지 않는다 — 서버는 `wh.jandi.com` 에 붙지 못한다
       (WAS·APP 모두 403, 2026-08-18 실측). DB_PC 릴레이가 SSH 터널로 가져간다.
    """

    # ⛔ 주말에는 만들지도 보내지도 않는다. 잡은 매일 09:00 인데 **릴레이는 평일에만
    #    돈다** — 토·일 몫이 대기열에 쌓였다가 월요일 아침에 세 통이 한꺼번에 나간다.
    #    그중 둘은 이미 지난 날의 "오늘 일정" 이라 틀린 내용이다.
    #    출근 브리핑은 출근하는 날의 것이다 (2026-08-26, 공지 전 점검에서 발견).
    #    ⚠️ 공휴일은 여기서 거르지 않는다 — 판정에 구글 캘린더가 필요해 잡 전체가
    #       외부 호출에 묶인다. 공휴일 브리핑은 그날 하루 어색할 뿐이고, 메일 구간은
    #       `mail_window()` 가 이미 연휴를 제대로 처리한다.
    current = now or datetime.now(ZoneInfo("Asia/Seoul"))
    if workday.is_weekend(current.date()):
        logger.info("personal_briefing_skipped_weekend", day=str(current.date()))
        return {"selected": 0, "succeeded": 0, "failed": 0, "queued": 0,
                "skipped": "weekend"}

    rows = await asyncio.to_thread(
        fetch_all,
        "SELECT u.id,COALESCE(a.email,u.email) email,COALESCE(a.display_name,u.display_name) name,"
        "COALESCE(a.department,'') department,u.role,u.allowed_models,u.ad_user_id "
        "FROM users u LEFT JOIN ad_users a ON a.id=u.ad_user_id "
        # ⛔ `last_login` 으로 거르지 마라 — `/signin` 에서만 찍혀서 가입 직후
        #    자동 로그인된 사람은 영영 NULL 이다 (활성 63명 중 28명, 2026-08-26 실측).
        #    NULL 은 "안 쓴다" 가 아니라 "모른다" 다. 진짜 게이트는 아래 `has_credentials`
        #    다 — 구글을 연결했다는 것 자체가 쓰겠다는 의사표시다.
        "WHERE u.is_active=1 "
        "  AND (u.last_login IS NULL "
        "       OR u.last_login >= DATE_SUB(NOW(), INTERVAL 30 DAY)) "
        "AND COALESCE(a.department,'') NOT LIKE %s",
        ("%퇴사%",),
    )
    semaphore = asyncio.Semaphore(3)
    selected = [row for row in rows if _auth_manager.has_credentials(row["email"])]
    try:
        webhooks = {
            int(entry["user_id"]): str(entry["webhook_url"])
            for entry in await asyncio.to_thread(jandi_briefing.enabled_recipients)
        }
    except Exception as exc:
        logger.warning("jandi_recipients_unavailable", error_type=type(exc).__name__)
        webhooks = {}
    queued = 0

    async def one(row: dict[str, Any]) -> dict[str, Any]:
        nonlocal queued
        async with semaphore:
            user = User(
                id=row["id"], email=row["email"], name=row["name"],
                department=row["department"], role=row["role"],
                allowed_models=row["allowed_models"], ad_user_id=row["ad_user_id"],
            )
            result = await refresh_for_user(user, now=now, force=True)
            url = webhooks.get(int(row["id"]))
            if url:
                if await asyncio.to_thread(
                    _enqueue_jandi, user, result, url, row.get("name", ""),
                ):
                    queued += 1
            return result

    results = await asyncio.gather(*(one(row) for row in selected), return_exceptions=True)
    await asyncio.to_thread(store.cleanup, briefing_window(now)[0] - timedelta(days=1))
    return {
        "selected": len(selected),
        "succeeded": sum(not isinstance(result, Exception) for result in results),
        "failed": sum(isinstance(result, Exception) for result in results),
        "queued": queued,
    }


def _enqueue_jandi(user: User, envelope: dict[str, Any], url: str, name: str) -> bool:
    """문서가 실제로 만들어졌을 때만 대기열에 넣는다 — 빈 브리핑을 보내지 않는다."""

    document = envelope.get("document") or {}
    if document.get("status") not in {"ready", "empty"}:
        return False
    body = work_briefing.render_markdown(
        document, name=name or user.name or "", fx=envelope.get("fx"),
        business=envelope.get("business"),
    )
    if not body.strip():
        return False
    # ⚠️ 잔디에서 읽고 끝나면 셀라에 오지 않는다 — 돌아올 문을 매번 열어 둔다.
    #    실측(2026-08-26): 브리핑 열람률 8.1%, 그런데 열어본 날 질문 전환은 23.7%였다.
    from app.core.jandi_notify import base_url

    link = base_url()
    if link:
        body += f"\n\n이어서 물어보기 · {link}"
    for_date = date.fromisoformat(str(envelope.get("for_date", "")))
    try:
        return jandi_briefing.enqueue(
            user.id, for_date, url, body,
            kind="briefing", dedup_key=f"briefing:{for_date}",
            title="오늘의 출근 브리핑", link=link,
        )
    except Exception as exc:
        logger.warning("jandi_enqueue_failed", user_id=user.id, error_type=type(exc).__name__)
        return False
