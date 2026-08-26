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


def test_card_summary_is_derived_from_the_document(monkeypatch):
    """카드와 문서가 각자 문장을 만들면 한 화면에서 서로 다른 말을 한다 — 사본을 두지 않는다."""
    monkeypatch.setattr(pb.work_briefing, "generate", lambda *_a, **_k: {
        "mail_summary": "결재 요청이 있습니다.",
        "mail_points": [
            {"message_id": "real", "points": ["결재 대기"], "request": "확인 요청"},
            {"message_id": "invented", "points": ["없는 메일"], "request": "무시"},
        ],
    })
    calendar = {"status": "empty", "items": []}
    mail = {"status": "ready", "count_label": "1건", "unread": 1, "truncated": False,
            "error_code": "", "items": [
                {"id": "real", "subject": "결재", "from_display": "A", "snippet": "확인",
                 "url": "https://mail.google.com/mail/u/0/#all/real", "unread": True}]}
    document = pb.build_document(calendar, mail, {}, date(2026, 8, 25),
                                 datetime(2026, 8, 25, 9, 0, tzinfo=SEOUL))
    _calendar, merged = pb._apply_document(calendar, mail, document)

    assert [row["id"] for row in document["mail"]] == ["real"]
    assert merged["summary"] == "결재 요청이 있습니다."
    assert [x["message_id"] for x in merged["action_candidates"]] == ["real"]
    assert "snippet" not in merged["items"][0]


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
    monkeypatch.setattr(pb.briefing, "latest_for_user", lambda *_a, **_k: {"title": "old secret"})
    result = pb._business_for_user(7)
    assert result == {"status": "disabled", "item": None}


def test_business_card_splits_sales_marketing_and_roas(monkeypatch):
    """지표는 **매출 · 마케팅 · ROAS 세 줄**이다 (2026-08-26 사용자 요청).

    ⛔ 한 덩어리로 두면 어느 수치가 어느 쪽인지 읽는 사람이 갈라야 한다.
    ⛔ 저장된 알림 행이 아니라 **최신 집계**에서 만든다 — 조용한 날엔 알림이 없어
       며칠 전 수치가 붙는다. 저장 행에서는 관심 축(scope)만 가져온다.
    """
    monkeypatch.setattr(pb.briefing, "is_opted_out", lambda _uid: False)
    # 화면 지표는 알림 여부와 무관하게 최신 하나를 본다 (`for_user` 는 알림함용).
    monkeypatch.setattr(pb.briefing, "latest_for_user", lambda *_a, **_k: {
        "id": 11,
        "for_date": date(2026, 8, 23),
        "scope": "동남아시아1팀",
        "title": "동남아시아1팀 매출 변화",
        "body": "· 동남아시아1팀 전체 · 매출 10.0억",
        "follow_up": "자세히",
    })
    monkeypatch.setattr(pb.briefing, "latest_sales_snapshot", lambda: {
        "base": date(2026, 8, 24),
        "cur_from": date(2026, 8, 18),
        "prev_from": date(2026, 8, 11),
        "prev_to": date(2026, 8, 17),
        "by_team": {"EAST1": {"now": 10.0e8, "prev": 8.0e8}},
        "by_country": {}, "changes": [],
    }, raising=False)
    monkeypatch.setattr(pb.briefing, "latest_marketing_snapshot", lambda: {
        "base": date(2026, 8, 24),
        "cur_from": date(2026, 8, 18),
        "prev_from": date(2026, 8, 11),
        "prev_to": date(2026, 8, 17),
        "by_team": {"EAST1": {
            "now_cost": 2.5e8, "prev_cost": 2e8,
            "now_clicks": 1_234, "prev_clicks": 1_100,
            "now_conv": 7.5e8, "prev_conv": 5.0e8,
        }},
        "by_country": {}, "all": {},
    }, raising=False)

    result = pb._business_for_user(7)

    assert result["status"] == "ready"
    kinds = [row["kind"] for row in result["items"]]
    assert kinds == ["sales", "marketing", "roas"], kinds
    sales, marketing, roas = result["items"]
    assert sales["kind"] == "sales"
    # 최신 집계에서 만든 값이다 (저장 행의 "매출 변화" 가 아니다).
    # 눈에 띄게 움직인 국가가 없으면 축 합계로 말하되 기준을 밝힌다.
    assert "매출 10.0억" in sales["title"]
    assert "+25%" in sales["title"]
    assert "눈에 띄게 움직인 국가 없음" in sales["body"]
    assert sales["for_date"] == "2026-08-24"
    # 매출 줄에 마케팅이 섞여 있으면 안 된다.
    assert "마케팅" not in sales["title"] + sales["body"]

    assert marketing["kind"] == "marketing"
    assert "광고비 2.5억" in marketing["title"]
    assert "클릭 1,234회" in marketing["body"]
    # 광고 기준일이 매출과 다를 수 있다 — 자기 날짜를 들고 온다.
    assert marketing["for_date"] == "2026-08-24"
    # ROAS = 전환매출 7.5억 ÷ 광고비 2.5억 = 3.00 (나눌 국가가 없으면 합계)
    assert "ROAS 3.00" in roas["title"]
    # ⛔ 광고 플랫폼 자체 집계값이다 — 실매출이 아니라는 것이 본문에 남아야 한다.
    assert "플랫폼 자체 집계값" in roas["body"]
    # 기존 키(`item`)는 우선순위·옵트아웃 판정이 쓰므로 매출 쪽을 그대로 둔다.
    assert result["item"] is sales


def test_past_today_event_is_marked_ended():
    raw = {"items": [{
        "id": "e1", "summary": "어제 회의", "start": "2026-08-25T08:00:00+09:00",
        "end": "2026-08-25T09:00:00+09:00", "location": "", "htmlLink": "",
    }], "truncated": False}
    section = pb._normalize_calendar(raw, datetime(2026, 8, 25, 10, 0, tzinfo=SEOUL))
    assert section["items"][0]["ended"] is True


def test_calendar_normalization_allows_approved_www_google_calendar_url():
    raw = {"items": [{
        "id": "e1", "summary": "회의", "start": "2026-08-25T08:00:00+09:00",
        "end": "2026-08-25T09:00:00+09:00", "location": "",
        "htmlLink": "https://www.google.com/calendar/event?eid=e1",
    }], "truncated": False}

    section = pb._normalize_calendar(raw, datetime(2026, 8, 25, 7, 0, tzinfo=SEOUL))

    assert section["items"][0]["url"] == "https://www.google.com/calendar/event?eid=e1"
    assert pb._safe_google_link("https://www.google.com/calendar.evil/event") == ""


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


def test_document_failure_keeps_deterministic_mail_items():
    """LLM 이 죽어도 메일 목록은 조회 결과라 그대로 남아야 한다."""
    mail = {"status": "ready", "items": [{"id": "m1", "subject": "제목", "snippet": "본문"}],
            "count_label": "1건", "unread": 1, "truncated": False, "error_code": ""}
    document = pb._document_only_facts(
        {"status": "empty", "items": []}, mail, {},
        date(2026, 8, 25), datetime(2026, 8, 25, 9, 0, tzinfo=SEOUL),
    )
    document["status"] = "error"
    _calendar, result = pb._apply_document({"status": "empty", "items": []}, mail, document)

    assert result["status"] == "ready"
    assert result["error_code"] == "summary_failed"
    assert result["items"][0]["subject"] == "제목"
    assert "snippet" not in result["items"][0]
    assert result["summary"] == ""
    assert document["mail_total"] == 1


def test_cached_lookup_requests_only_today(monkeypatch):
    seen = {}
    monkeypatch.setattr(pb._auth_manager, "has_credentials", lambda _email: False)
    monkeypatch.setattr(pb._auth_manager, "get_stored_google_email", lambda _email: "")
    monkeypatch.setattr(pb.store, "get_snapshot", lambda uid, day: seen.update(uid=uid, day=day) or None)
    monkeypatch.setattr(pb, "_business_for_user", lambda _uid: {"status": "empty", "item": None})
    user = type("U", (), {"id": 7, "email": "owner@example.com"})()
    pb.get_cached_for_user(user, datetime(2026, 8, 25, 9, 0, tzinfo=SEOUL))
    assert seen == {"uid": 7, "day": date(2026, 8, 25)}


@pytest.mark.asyncio
async def test_account_switch_never_reuses_old_account_sections(monkeypatch):
    now = datetime(2026, 8, 25, 9, 0, tzinfo=SEOUL)
    user = pb.User(id=7, email="owner@example.com")
    old_snapshot = {
        "google_account_hash": pb._account_hash("old@example.com"),
        "calendar": {"status": "ready", "items": [{"id": "old-event"}], "truncated": False, "error_code": ""},
        "mail": {"status": "ready", "items": [{"id": "old-mail"}], "truncated": False, "error_code": ""},
        "priorities": [{"source": "calendar", "source_id": "old-event", "title": "old"}],
        "generated_at": now - timedelta(minutes=11),
    }
    persisted = {}

    monkeypatch.setattr(pb._auth_manager, "has_credentials", lambda _email: True)
    monkeypatch.setattr(pb._auth_manager, "get_stored_google_email", lambda _email: "new@example.com")
    monkeypatch.setattr(
        pb._auth_manager, "load_credentials",
        lambda _email: pb.CredentialLoadOutcome(status="ready", credentials=object()),
    )
    monkeypatch.setattr(pb._auth_manager, "get_credential_identity", lambda _email: "new-credential")
    monkeypatch.setattr(pb.store, "get_snapshot", lambda *_args: old_snapshot)
    monkeypatch.setattr(pb, "list_calendar_window", lambda *_args: (_ for _ in ()).throw(TimeoutError()))
    monkeypatch.setattr(pb, "list_gmail_digest", lambda *_args: {"items": [], "truncated": False})
    monkeypatch.setattr(pb, "_business_for_user", lambda _uid: {"status": "empty", "item": None})

    def put_snapshot(_uid, _day, account_hash, calendar, mail, priorities, _generated, _document=None):
        persisted.update(account_hash=account_hash, calendar=calendar, mail=mail, priorities=priorities)

    monkeypatch.setattr(pb.store, "put_snapshot", put_snapshot)
    result = await pb.refresh_for_user(user, now=now, force=True)

    assert result["calendar"]["status"] == "error"
    assert result["calendar"]["items"] == []
    assert persisted["account_hash"] == pb._account_hash("new@example.com")
    assert persisted["calendar"]["items"] == []
    assert persisted["priorities"] == []
    assert "old-event" not in str(persisted)
    assert "old-mail" not in str(persisted)


def test_opted_out_cached_business_priority_is_suppressed(monkeypatch):
    now = datetime(2026, 8, 25, 9, 0, tzinfo=SEOUL)
    user = pb.User(id=7, email="owner@example.com")
    snapshot = {
        "google_account_hash": pb._account_hash("owner@example.com"),
        "calendar": {"status": "empty", "items": [], "truncated": False, "error_code": ""},
        "mail": {"status": "empty", "items": [], "truncated": False, "error_code": ""},
        "priorities": [
            {"source": "business", "source_id": "secret", "title": "old business"},
            {"source": "mail", "source_id": "m1", "title": "safe"},
        ],
        "generated_at": now,
    }
    monkeypatch.setattr(pb._auth_manager, "has_credentials", lambda _email: True)
    monkeypatch.setattr(pb._auth_manager, "get_stored_google_email", lambda _email: "owner@example.com")
    monkeypatch.setattr(pb.store, "get_snapshot", lambda *_args: snapshot)
    monkeypatch.setattr(pb, "_business_for_user", lambda _uid: {"status": "disabled", "item": None})

    result = pb.get_cached_for_user(user, now)

    assert result["business"]["status"] == "disabled"
    assert result["priorities"] == [{"source": "mail", "source_id": "m1", "title": "safe"}]


def test_business_lookup_error_hides_cached_business_priority(monkeypatch):
    now = datetime(2026, 8, 25, 9, 0, tzinfo=SEOUL)
    user = pb.User(id=7, email="owner@example.com")
    snapshot = {
        "google_account_hash": pb._account_hash("owner@example.com"),
        "calendar": {"status": "empty", "items": [], "truncated": False, "error_code": ""},
        "mail": {"status": "empty", "items": [], "truncated": False, "error_code": ""},
        "priorities": [
            {"source": "business", "source_id": "unknown", "title": "must hide"},
            {"source": "calendar", "source_id": "e1", "title": "safe"},
        ],
        "generated_at": now,
    }
    monkeypatch.setattr(pb._auth_manager, "has_credentials", lambda _email: True)
    monkeypatch.setattr(pb._auth_manager, "get_stored_google_email", lambda _email: "owner@example.com")
    monkeypatch.setattr(pb.store, "get_snapshot", lambda *_args: snapshot)
    monkeypatch.setattr(pb, "_safe_business_for_user", lambda _uid: {"status": "error", "item": None})

    result = pb.get_cached_for_user(user, now)

    assert result["business"]["status"] == "error"
    assert result["priorities"] == [{"source": "calendar", "source_id": "e1", "title": "safe"}]


def test_cached_business_priority_must_match_current_ready_item(monkeypatch):
    now = datetime(2026, 8, 25, 9, 0, tzinfo=SEOUL)
    user = pb.User(id=7, email="owner@example.com")
    snapshot = {
        "google_account_hash": pb._account_hash("owner@example.com"),
        "calendar": {"status": "empty", "items": [], "truncated": False, "error_code": ""},
        "mail": {"status": "empty", "items": [], "truncated": False, "error_code": ""},
        "priorities": [
            {"source": "business", "source_id": "old", "title": "old business"},
            {"source": "business", "source_id": "current", "title": "current business"},
            {"source": "calendar", "source_id": "e1", "title": "safe"},
        ],
        "generated_at": now,
    }
    monkeypatch.setattr(pb._auth_manager, "has_credentials", lambda _email: True)
    monkeypatch.setattr(pb._auth_manager, "get_stored_google_email", lambda _email: "owner@example.com")
    monkeypatch.setattr(pb.store, "get_snapshot", lambda *_args: snapshot)
    monkeypatch.setattr(
        pb,
        "_safe_business_for_user",
        lambda _uid: {"status": "ready", "item": {"id": "current", "title": "current business"}},
    )

    result = pb.get_cached_for_user(user, now)

    assert [item["source_id"] for item in result["priorities"]] == ["current", "e1"]


@pytest.mark.asyncio
async def test_malformed_calendar_stales_only_calendar_and_keeps_mail(monkeypatch):
    now = datetime(2026, 8, 25, 9, 0, tzinfo=SEOUL)
    user = pb.User(id=7, email="owner@example.com")
    snapshot = {
        "google_account_hash": pb._account_hash("owner@example.com"),
        "calendar": {"status": "ready", "items": [{"id": "prior-event"}], "truncated": False, "error_code": ""},
        "mail": {"status": "empty", "items": [], "truncated": False, "error_code": ""},
        "priorities": [],
        "generated_at": now - timedelta(minutes=11),
    }
    persisted = {}

    monkeypatch.setattr(pb._auth_manager, "has_credentials", lambda _email: True)
    monkeypatch.setattr(pb._auth_manager, "get_stored_google_email", lambda _email: "owner@example.com")
    monkeypatch.setattr(
        pb._auth_manager, "load_credentials",
        lambda _email: pb.CredentialLoadOutcome(status="ready", credentials=object()),
    )
    monkeypatch.setattr(pb._auth_manager, "get_credential_identity", lambda _email: "owner-credential")
    monkeypatch.setattr(pb.store, "get_snapshot", lambda *_args: snapshot)
    monkeypatch.setattr(pb, "list_calendar_window", lambda *_args: {
        "items": [{"id": "bad", "summary": "bad", "start": "not-a-date", "end": "not-a-date"}], "truncated": False,
    })
    monkeypatch.setattr(pb, "list_gmail_digest", lambda *_args: {
        "items": [{"id": "m1", "thread_id": "t1", "subject": "new mail", "from": "A",
                   "received_at": "2026-08-25T08:00:00+09:00", "unread": True, "snippet": "preview",
                   "url": "https://mail.google.com/mail/u/0/#all/m1"}], "truncated": False,
    })
    monkeypatch.setattr(pb, "_business_for_user", lambda _uid: {"status": "empty", "item": None})

    monkeypatch.setattr(pb.work_briefing, "generate", lambda *_a, **_k: {})
    monkeypatch.setattr(
        pb.store, "put_snapshot",
        lambda _uid, _day, _hash, calendar, mail, priorities, _generated, _document=None: persisted.update(
            calendar=calendar, mail=mail, priorities=priorities,
        ),
    )

    result = await pb.refresh_for_user(user, now=now, force=True)

    assert result["calendar"]["status"] == "stale"
    assert result["calendar"]["items"] == [{"id": "prior-event"}]
    assert result["mail"]["status"] == "ready"
    assert result["mail"]["items"][0]["id"] == "m1"
    assert persisted["mail"]["items"][0]["id"] == "m1"


@pytest.mark.asyncio
async def test_expired_credentials_clear_prior_content_and_snapshot(monkeypatch):
    """A stale token file must yield reconnect UI without prior-account content."""
    now = datetime(2026, 8, 25, 9, 0, tzinfo=SEOUL)
    user = pb.User(id=7, email="owner@example.com")
    cached = {
        "enabled": True, "for_date": "2026-08-25", "needs_refresh": True,
        "google": {"connected": True, "account": "old-google@example.com"},
        "calendar": {"status": "ready", "items": [{"id": "old-event"}], "error_code": ""},
        "mail": {"status": "ready", "items": [{"id": "old-mail"}], "error_code": ""},
        "priorities": [{"source": "mail", "source_id": "old-mail"}],
        "business": {"status": "empty", "item": None},
    }
    deleted = {}
    monkeypatch.setattr(pb, "get_cached_for_user", lambda *_args: dict(cached))
    monkeypatch.setattr(
        pb._auth_manager, "load_credentials",
        lambda _email: pb.CredentialLoadOutcome(status="invalid", error_code="oauth_expired"),
    )
    monkeypatch.setattr(pb._auth_manager, "revoke_credentials", lambda email: deleted.update(email=email) or True)
    monkeypatch.setattr(pb.store, "delete_for_user", lambda user_id: deleted.update(user_id=user_id))

    result = await pb.refresh_for_user(user, now=now, force=True)

    assert deleted == {"email": "owner@example.com", "user_id": 7}
    assert result["google"] == {"connected": False, "account": ""}
    assert result["calendar"]["status"] == "disconnected"
    assert result["mail"]["status"] == "disconnected"
    assert result["calendar"]["items"] == []
    assert result["mail"]["items"] == []
    assert result["priorities"] == []


@pytest.mark.asyncio
async def test_transient_credential_failure_preserves_snapshot_and_returns_stale(monkeypatch):
    """Temporary credential failures must not disconnect or erase same-day content."""
    now = datetime(2026, 8, 25, 9, 0, tzinfo=SEOUL)
    user = pb.User(id=7, email="owner@example.com")
    cached = {
        "enabled": True, "for_date": "2026-08-25", "needs_refresh": True,
        "google": {"connected": True, "account": "connected@example.com"},
        "calendar": {"status": "ready", "items": [{"id": "saved-event"}], "error_code": ""},
        "mail": {"status": "ready", "items": [{"id": "saved-mail"}], "error_code": ""},
        "priorities": [{"source": "mail", "source_id": "saved-mail"}],
        "business": {"status": "empty", "item": None},
    }
    destructive_calls = []
    monkeypatch.setattr(pb, "get_cached_for_user", lambda *_args: dict(cached))
    monkeypatch.setattr(
        pb._auth_manager,
        "load_credentials",
        lambda _email: pb.CredentialLoadOutcome(status="transient_error", error_code="google_error"),
    )
    monkeypatch.setattr(
        pb._auth_manager,
        "revoke_credentials",
        lambda _email: destructive_calls.append("revoke"),
    )
    monkeypatch.setattr(
        pb.store,
        "delete_for_user",
        lambda _user_id: destructive_calls.append("delete"),
    )

    result = await pb.refresh_for_user(user, now=now, force=True)

    assert destructive_calls == []
    assert result["google"] == cached["google"]
    assert result["calendar"]["items"] == [{"id": "saved-event"}]
    assert result["mail"]["items"] == [{"id": "saved-mail"}]
    assert result["calendar"]["status"] == "stale"
    assert result["mail"]["status"] == "stale"
    assert result["calendar"]["error_code"] == "google_error"
    assert result["mail"]["error_code"] == "google_error"
    assert result["needs_refresh"] is False


@pytest.mark.asyncio
async def test_refresh_revalidates_credential_identity_before_persistence(monkeypatch):
    """An externally switched credential cannot persist data fetched for the prior account."""
    now = datetime(2026, 8, 25, 9, 0, tzinfo=SEOUL)
    user = pb.User(id=7, email="owner@example.com")
    cached = {
        "enabled": True, "for_date": "2026-08-25", "needs_refresh": True,
        "google": {"connected": True, "account": "old-google@example.com"},
        "calendar": {"status": "empty", "items": [], "error_code": ""},
        "mail": {"status": "empty", "items": [], "error_code": ""},
        "priorities": [], "business": {"status": "empty", "item": None},
    }
    identities = iter(("credential-a", "credential-b"))
    account_calls = iter(("old-google@example.com", "new-google@example.com"))
    monkeypatch.setattr(pb, "get_cached_for_user", lambda *_args: dict(cached))
    monkeypatch.setattr(
        pb._auth_manager, "load_credentials",
        lambda _email: pb.CredentialLoadOutcome(status="ready", credentials=object()),
    )
    monkeypatch.setattr(pb._auth_manager, "get_credential_identity", lambda _email: next(identities))
    monkeypatch.setattr(pb._auth_manager, "get_stored_google_email", lambda _email: next(account_calls))
    monkeypatch.setattr(pb.store, "get_snapshot", lambda *_args: None)
    monkeypatch.setattr(pb, "_refresh_sections", lambda *_args: asyncio.sleep(0, result=({"items": []}, {"items": []})))
    monkeypatch.setattr(
        pb.store,
        "put_snapshot",
        lambda *_args: (_ for _ in ()).throw(AssertionError("prior credential persisted")),
    )
    monkeypatch.setattr(pb.store, "delete_for_user", lambda _user_id: None)

    result = await pb.refresh_for_user(user, now=now, force=True)

    assert result["google"]["account"] != "old-google@example.com"
    assert result["calendar"]["items"] == []


@pytest.mark.parametrize("raw,expected", [
    ('"Fred from Fireflies.ai" <fred@fireflies.ai>', "Fred from Fireflies.ai"),
    ("빅스데이터 마케팅팀 <mkt@bigxdata.io>", "빅스데이터 마케팅팀"),
    ("=?UTF-8?B?7J207ZW07J24?= <haein@skin1004korea.com>", "이해인"),
    ("noreply@github.com", "noreply"),
    ("<solo@example.com>", "solo"),
    ("", ""),
])
def test_sender_display_keeps_the_name_not_the_header(raw, expected):
    """원본 From 을 그대로 실으면 브리핑 한 줄의 절반을 메일 주소가 먹는다."""
    assert pb._sender_display(raw) == expected


def test_never_ending_null_last_login_excludes_a_connected_user():
    """⛔ `last_login` 으로 사전 생성 대상을 거르지 마라 (2026-08-26 이해인 님 제보).

    `/signin` 에서만 찍히는 값이라 가입 직후 자동 로그인된 사람은 영영 NULL 이다
    (활성 63명 중 28명이 그랬다). 구글을 연결했는데도 09:00 잡에서 빠졌다.
    진짜 게이트는 `has_credentials` 다.
    """
    import inspect

    source = inspect.getsource(pb.run_morning_precompute)
    assert "u.last_login IS NULL" in source, "NULL 을 포함하지 않는다"
    assert "has_credentials" in source


def test_signup_stamps_last_login():
    """가입하면 곧바로 로그인 상태다 — 그때 찍지 않으면 다시 찍을 기회가 없다."""
    from pathlib import Path

    source = (Path(__file__).resolve().parent.parent
              / "app" / "api" / "auth_api.py").read_text(encoding="utf-8")
    insert = source.split("INSERT INTO users", 1)[1][:300]
    assert "last_login" in insert, insert


def test_a_dead_model_still_keeps_todays_events():
    """⛔ 일정은 조회 결과라 LLM 과 무관하다 — 함께 버리면 '일정 없음' 으로 보인다."""
    calendar = {"status": "ready", "items": [{
        "id": "e1", "title": "본부장 월간회의",
        "start": "2026-08-25T10:00:00+09:00", "end": "2026-08-25T12:00:00+09:00",
        "all_day": False, "location": "Creation (3F)", "url": "", "ended": False,
    }]}
    mail = {"status": "ready", "items": [{"id": "m1", "subject": "제목", "snippet": "본문"}],
            "count_label": "1건", "unread": 1, "truncated": False, "error_code": ""}
    document = pb._document_only_facts(
        calendar, mail, {}, date(2026, 8, 25), datetime(2026, 8, 25, 9, 0, tzinfo=SEOUL),
    )
    assert [row["id"] for row in document["meetings"]] == ["e1"]
    assert document["mail_total"] == 1
