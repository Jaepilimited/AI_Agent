"""Read-only, cached aggregation for a user's login work briefing.

Only compact Gmail metadata and Calendar event metadata cross this boundary.
Raw snippets are used transiently for the optional mail summary and are stripped
before a result is returned or persisted.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import date, datetime, time, timedelta
from typing import Any, Callable
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from app.core import briefing
from app.core.google_auth import CredentialLoadOutcome, GoogleAuthManager
from app.core.google_workspace import list_calendar_window, list_gmail_digest
from app.core.llm import get_flash_client
from app.core import personal_briefing_store as store
from app.db.mariadb import fetch_all
from app.db.models import User


SEOUL = ZoneInfo("Asia/Seoul")
CACHE_TTL = timedelta(minutes=10)
GOOGLE_TIMEOUT_SECONDS = 10.0
SUMMARY_TIMEOUT_SECONDS = 8.0
REFRESH_TIMEOUT_SECONDS = 15.0
_ALLOWED_LINKS = {"mail.google.com", "calendar.google.com"}

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
        })
    return {
        "status": "ready" if items else "empty",
        "items": items,
        "truncated": bool(raw.get("truncated", False)),
        "error_code": "",
    }


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
            "from_display": str(item.get("from", item.get("from_display", ""))),
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


def _generate_mail_json(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Ask Flash only for a compact wording over transient, bounded snippets."""

    prompt_items = [{
        "message_id": item["id"],
        "subject": item["subject"],
        "from": item.get("from_display", item.get("from", "")),
        "snippet": item.get("snippet", ""),
    } for item in items]
    prompt = (
        "다음 오늘 받은 메일 미리보기만 사용하세요. 전체 요약은 2문장 이하, "
        "확인 필요는 최대 3개로 JSON을 반환하세요. 확인 필요 message_id는 입력값만 복사하세요. "
        "건수·시간·보냄·마감일을 추측하지 마세요.\n" +
        json.dumps(prompt_items, ensure_ascii=False)
    )
    text = get_flash_client().generate_json(prompt, temperature=0.0, max_output_tokens=1200)
    value = json.loads(text)
    return {
        "summary": str(value.get("summary", ""))[:500],
        "action_candidates": list(value.get("action_candidates", []))[:3],
    }


def summarize_mail(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Drop every LLM action candidate not tied to a collected message id."""

    generated = _generate_mail_json(items) if items else {"summary": "", "action_candidates": []}
    by_id = {str(item.get("id", "")): item for item in items}
    valid = []
    for candidate in generated.get("action_candidates", []):
        if not isinstance(candidate, dict):
            continue
        message_id = str(candidate.get("message_id", ""))
        if message_id in by_id:
            valid.append({"message_id": message_id, "reason": str(candidate.get("reason", ""))[:160]})
    return {"summary": str(generated.get("summary", ""))[:500], "action_candidates": valid[:3]}


async def _summarize_mail_async(items: list[dict[str, Any]]) -> dict[str, Any]:
    return await asyncio.wait_for(
        asyncio.to_thread(summarize_mail, items), timeout=SUMMARY_TIMEOUT_SECONDS,
    )


async def _attach_summary(mail: dict[str, Any]) -> dict[str, Any]:
    """Keep deterministic mail metadata when optional LLM formatting fails."""

    result = dict(mail)
    try:
        result.update(await _summarize_mail_async(result.get("items", [])))
    except Exception:
        result["summary"] = ""
        result["action_candidates"] = []
        result["error_code"] = "summary_failed"
    result["items"] = [
        {key: value for key, value in item.items() if key != "snippet"}
        for item in result.get("items", [])
    ]
    return result


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
    rows = briefing.for_user(user_id, limit=1)
    if not rows:
        return {"status": "empty", "item": None}
    row = rows[0]
    return {"status": "ready", "item": {
        "id": str(row["id"]), "for_date": str(row["for_date"]),
        "title": row.get("title", ""), "body": row.get("body", ""),
        "follow_up": row.get("follow_up", ""),
    }}


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
        generated = snapshot["generated_at"]
    else:
        calendar, mail = _empty_sections("disconnected" if not connected else "empty", "oauth_missing" if not connected else "")
        priorities, generated = [], None
    business = _safe_business_for_user(user.id)
    return {
        "enabled": True, "for_date": str(day), "timezone": "Asia/Seoul",
        "generated_at": _aware(generated).isoformat() if generated else "",
        "needs_refresh": connected and _needs_refresh(generated, current),
        "google": {"connected": connected, "account": account},
        "priorities": _visible_priorities(priorities, business), "calendar": calendar, "mail": mail,
        "business": business,
    }


async def _collect_with_timeout(fn: Callable[..., Any], *args: Any) -> Any:
    return await asyncio.wait_for(asyncio.to_thread(fn, *args), timeout=GOOGLE_TIMEOUT_SECONDS)


def _error_code(exc: BaseException) -> str:
    text = str(exc).lower()
    if "quota" in text or "429" in text:
        return "google_quota"
    if "refresh" in text or "invalid_grant" in text:
        return "oauth_expired"
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
        return "google_timeout"
    return "google_error"


async def _refresh_sections(creds: Any, start: datetime, end: datetime, current: datetime) -> tuple[Any, Any]:
    """Run Google calls concurrently; Gmail summary is the only optional follow-up."""

    calendar_raw, mail_raw = await asyncio.gather(
        _collect_with_timeout(list_calendar_window, creds, start, end, 50),
        _collect_with_timeout(list_gmail_digest, creds, start, current, 20),
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
                generated_at="",
                needs_refresh=False,
            )
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
        try:
            calendar_raw, mail_raw = await asyncio.wait_for(
                _refresh_sections(creds, start, end, current), timeout=REFRESH_TIMEOUT_SECONDS,
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
                mail = await _attach_summary(_normalize_mail(mail_raw))
            except Exception:
                mail = _merge_failed_section(previous_mail, "google_error")
                defaults = _empty_sections("error", "")[1]
                for key, value in defaults.items():
                    mail.setdefault(key, value)

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
            }
        generated_at = current.replace(tzinfo=None)
        await asyncio.to_thread(
            store.put_snapshot, user.id, day, _account_hash(account),
            calendar, mail, priorities, generated_at,
        )
        return {
            "enabled": True, "for_date": str(day), "timezone": "Asia/Seoul",
            "generated_at": current.isoformat(), "needs_refresh": False,
            "google": {"connected": True, "account": account},
            "priorities": priorities, "calendar": calendar, "mail": mail, "business": business,
        }


async def run_morning_precompute(now: datetime | None = None) -> dict[str, int]:
    """Warm recent active users' snapshots, bounded to three concurrent refreshes."""

    rows = await asyncio.to_thread(
        fetch_all,
        "SELECT u.id,COALESCE(a.email,u.email) email,COALESCE(a.display_name,u.display_name) name,"
        "COALESCE(a.department,'') department,u.role,u.allowed_models,u.ad_user_id "
        "FROM users u LEFT JOIN ad_users a ON a.id=u.ad_user_id "
        "WHERE u.is_active=1 AND u.last_login >= DATE_SUB(NOW(), INTERVAL 30 DAY) "
        "AND COALESCE(a.department,'') NOT LIKE %s",
        ("%퇴사%",),
    )
    semaphore = asyncio.Semaphore(3)
    selected = [row for row in rows if _auth_manager.has_credentials(row["email"])]

    async def one(row: dict[str, Any]) -> dict[str, Any]:
        async with semaphore:
            user = User(
                id=row["id"], email=row["email"], name=row["name"],
                department=row["department"], role=row["role"],
                allowed_models=row["allowed_models"], ad_user_id=row["ad_user_id"],
            )
            return await refresh_for_user(user, now=now, force=True)

    results = await asyncio.gather(*(one(row) for row in selected), return_exceptions=True)
    await asyncio.to_thread(store.cleanup, briefing_window(now)[0] - timedelta(days=1))
    return {
        "selected": len(selected),
        "succeeded": sum(not isinstance(result, Exception) for result in results),
        "failed": sum(isinstance(result, Exception) for result in results),
    }
