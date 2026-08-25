import asyncio
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.core import personal_briefing as pb


SEOUL = ZoneInfo("Asia/Seoul")


def test_window_is_today_plus_six_days():
    day, start, end = pb.briefing_window(datetime(2026, 8, 25, 16, 0, tzinfo=SEOUL))
    assert str(day) == "2026-08-25"
    assert start.isoformat() == "2026-08-25T00:00:00+09:00"
    assert end.isoformat() == "2026-09-01T00:00:00+09:00"


def test_summary_drops_unknown_message_ids(monkeypatch):
    monkeypatch.setattr(pb, "_generate_mail_json", lambda _items: {
        "summary": "결재 요청이 있습니다.",
        "action_candidates": [
            {"message_id": "real", "reason": "확인 요청"},
            {"message_id": "invented", "reason": "없는 메일"},
        ],
    })
    items = [{"id": "real", "subject": "결재", "from": "A", "snippet": "확인"}]
    result = pb.summarize_mail(items)
    assert [x["message_id"] for x in result["action_candidates"]] == ["real"]


def test_priorities_reference_real_sources_only():
    result = pb.build_priorities(
        calendar={"status": "ready", "items": [{
            "id": "e1", "title": "회의", "start": "2026-08-25T10:00:00+09:00",
            "ended": False, "url": "https://calendar.google.com/event?eid=e1",
        }]},
        mail={"status": "ready", "items": [{"id": "m1", "subject": "결재", "url": "https://mail.google.com/mail/u/0/#all/m1"}],
              "action_candidates": [{"message_id": "m1", "reason": "확인 요청"}]},
        business={"status": "ready", "item": {"id": "b1", "title": "매출 변화", "follow_up": "자세히"}},
        now=datetime(2026, 8, 25, 9, 0, tzinfo=SEOUL),
    )
    assert [x["source"] for x in result] == ["calendar", "mail", "business"]
    assert all(x["source_id"] in {"e1", "m1", "b1"} for x in result)


def test_business_opt_out_hides_old_content(monkeypatch):
    monkeypatch.setattr(pb.briefing, "is_opted_out", lambda _uid: True)
    monkeypatch.setattr(pb.briefing, "for_user", lambda *_a, **_k: [{"title": "old secret"}])
    result = pb._business_for_user(7)
    assert result == {"status": "disabled", "item": None}


def test_past_today_event_is_marked_ended():
    raw = {"items": [{
        "id": "e1", "summary": "어제 회의", "start": "2026-08-25T08:00:00+09:00",
        "end": "2026-08-25T09:00:00+09:00", "location": "", "htmlLink": "",
    }], "truncated": False}
    section = pb._normalize_calendar(raw, datetime(2026, 8, 25, 10, 0, tzinfo=SEOUL))
    assert section["items"][0]["ended"] is True


def test_failed_section_reuses_only_same_day_cache_as_stale():
    previous = {"status": "ready", "items": [{"id": "e1"}], "error_code": ""}
    stale = pb._merge_failed_section(previous, "google_timeout")
    assert stale["status"] == "stale"
    assert stale["items"] == [{"id": "e1"}]
    assert stale["error_code"] == "google_timeout"
    assert pb._merge_failed_section(None, "google_timeout")["status"] == "error"


def test_cache_age_boundary_is_ten_minutes():
    now = datetime(2026, 8, 25, 9, 0, tzinfo=SEOUL)
    assert pb._needs_refresh(now - pb.CACHE_TTL, now) is True
    assert pb._needs_refresh(now - pb.CACHE_TTL + timedelta(seconds=1), now) is False


@pytest.mark.asyncio
async def test_summary_timeout_keeps_deterministic_mail_items(monkeypatch):
    async def timeout(*_a, **_k):
        raise asyncio.TimeoutError()

    monkeypatch.setattr(pb, "_summarize_mail_async", timeout)
    mail = {"status": "ready", "items": [{"id": "m1", "subject": "제목", "snippet": "본문"}],
            "count_label": "1건", "unread": 1, "truncated": False, "error_code": ""}
    result = await pb._attach_summary(mail)
    assert result["status"] == "ready"
    assert result["error_code"] == "summary_failed"
    assert result["items"][0]["subject"] == "제목"
    assert "snippet" not in result["items"][0]


def test_cached_lookup_requests_only_today(monkeypatch):
    seen = {}
    monkeypatch.setattr(pb._auth_manager, "has_credentials", lambda _email: False)
    monkeypatch.setattr(pb._auth_manager, "get_stored_google_email", lambda _email: "")
    monkeypatch.setattr(pb.store, "get_snapshot", lambda uid, day: seen.update(uid=uid, day=day) or None)
    monkeypatch.setattr(pb, "_business_for_user", lambda _uid: {"status": "empty", "item": None})
    user = type("U", (), {"id": 7, "email": "owner@example.com"})()
    pb.get_cached_for_user(user, datetime(2026, 8, 25, 9, 0, tzinfo=SEOUL))
    assert seen == {"uid": 7, "day": date(2026, 8, 25)}
