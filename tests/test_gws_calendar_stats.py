"""Calendar counts must use the requested period, never the next seven days."""
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from app.agents import gws_agent
from app.core import google_workspace


NOW = datetime(2026, 9, 8, 11, 30, tzinfo=ZoneInfo("Asia/Seoul"))


@pytest.mark.parametrize("question,start,end", [
    ("올해 미팅 몇번 했어", "2026-01-01T00:00:00+09:00", "2026-09-08T11:30:00+09:00"),
    ("올해 오늘까지 미팅 횟수", "2026-01-01T00:00:00+09:00", "2026-09-08T11:30:00+09:00"),
    ("이번년에 미팅 횟수", "2026-01-01T00:00:00+09:00", "2026-09-08T11:30:00+09:00"),
    ("작년 회의 돌아보기", "2025-01-01T00:00:00+09:00", "2026-01-01T00:00:00+09:00"),
    ("2025년 12월 미팅 몇번", "2025-12-01T00:00:00+09:00", "2026-01-01T00:00:00+09:00"),
    ("2026년 1월 1일부터 8월 31일까지 회의 횟수", "2026-01-01T00:00:00+09:00", "2026-09-01T00:00:00+09:00"),
    ("2025-12-20~2026-01-05 미팅 통계", "2025-12-20T00:00:00+09:00", "2026-01-06T00:00:00+09:00"),
    ("올해 2분기 회의 몇번", "2026-04-01T00:00:00+09:00", "2026-07-01T00:00:00+09:00"),
    ("올해 상반기 미팅 회고", "2026-01-01T00:00:00+09:00", "2026-07-01T00:00:00+09:00"),
    ("지난달 미팅 건수", "2026-08-01T00:00:00+09:00", "2026-09-01T00:00:00+09:00"),
    ("최근 3개월 회의 몇번", "2026-06-08T00:00:00+09:00", "2026-09-08T11:30:00+09:00"),
])
def test_statistics_period_is_exact(question, start, end):
    from app.core.calendar_stats import parse_request
    request = parse_request(question, now=NOW)
    assert request.start.isoformat() == start
    assert request.end.isoformat() == end


@pytest.mark.parametrize("question", [
    "미팅 몇번했어", "2월 30일 미팅 몇번", "2026-09-01~2026-01-01 회의 횟수",
    "1월부터 회의 몇번", "이번 분기 중 첫 두 주 미팅 몇번",
    "2025년 1월부터 2026년 3월까지 회의 횟수", "올해 1분기부터 2분기까지 회의 몇번",
    "올해 1월과 8월 미팅 몇번", "올해 5월 둘째 주 미팅 몇번",
    "올해 틱톡 미팅 횟수", "올해 김민수님과 미팅 횟수", "올해 외부 미팅 횟수",
    "올해 상반기부터 하반기까지 미팅 횟수", "지난달부터 이번달까지 미팅 횟수",
    "작년과 올해 미팅 횟수", "2025년 지난달 미팅 횟수",
])
def test_unknown_or_invalid_period_is_not_silently_replaced(question):
    from app.core.calendar_stats import parse_request
    with pytest.raises(ValueError):
        parse_request(question, now=NOW)


def event(key, title="기획 회의", start="2026-01-05T10:00:00+09:00",
          end="2026-01-05T11:00:00+09:00", **extra):
    return {"id": key, "summary": title, "start": {"dateTime": start},
            "end": {"dateTime": end}, "status": "confirmed", **extra}


def test_counts_each_instance_once_and_applies_all_exclusions():
    from app.core.calendar_stats import parse_request, summarize
    request = parse_request(
        "올해 미팅 몇번 했어 (단, 틱톡 데일리 미팅 , 데이터분석 파트 미팅은 제외해줘)", now=NOW)
    rows = [
        event("r1", recurringEventId="series"),
        event("r2", start="2026-02-05T10:00:00+09:00", end="2026-02-05T11:00:00+09:00", recurringEventId="series"),
        event("r2", start="2026-02-05T10:00:00+09:00", end="2026-02-05T11:00:00+09:00", recurringEventId="series"),
        event("excluded1", "[정기] 틱톡데일리 미팅"),
        event("excluded2", "데이터분석 파트 미팅"),
        event("cancelled", status="cancelled"),
        event("declined", attendees=[{"email": "me@example.com", "self": True, "responseStatus": "declined"}]),
        event("future", start="2026-09-09T10:00:00+09:00", end="2026-09-09T11:00:00+09:00"),
        event("overlap", start="2025-12-31T23:30:00+09:00", end="2026-01-01T00:30:00+09:00"),
        event("focus", eventType="focusTime"),
        {"id": "all-day", "summary": "휴가", "start": {"date": "2026-01-05"}, "end": {"date": "2026-01-06"}},
        event("solo", "보고서 작성"),
    ]
    result = summarize(rows, request)
    assert result["total"] == 2
    assert result["months"] == {"2026-01": 1, "2026-02": 1}
    assert result["excluded_by_title"] == 2
    assert request.excluded_titles == ("틱톡 데일리 미팅", "데이터분석 파트 미팅")


def test_completed_meeting_crossing_period_end_is_counted_by_start_date():
    from app.core.calendar_stats import parse_request, summarize
    request = parse_request("2026년 1월 31일 미팅 횟수", now=NOW)
    result = summarize([event("overnight", start="2026-01-31T23:30:00+09:00",
                              end="2026-02-01T00:30:00+09:00")], request)
    assert result["total"] == 1


def test_remaining_meetings_exclude_past_events():
    from app.core.calendar_stats import parse_request, summarize
    request = parse_request("올해 앞으로 미팅 몇 번 남았어", now=NOW)
    result = summarize([event("past"), event("future", start="2026-09-09T10:00:00+09:00",
                                            end="2026-09-09T11:00:00+09:00")], request)
    assert result["total"] == 1
    assert request.start == NOW


def test_team_request_after_exclusion_is_preserved():
    from app.core.calendar_stats import parse_request
    request = parse_request("올해 미팅 몇번 (단, 데일리 미팅은 제외) 팀별로 알려줘", now=NOW)
    assert request.wants_teams
    assert request.excluded_titles == ("데일리 미팅",)


def test_team_counts_are_per_meeting_and_unknown_attendees_are_disclosed():
    from app.core.calendar_stats import parse_request, summarize, render
    request = parse_request("올해 캘린더 미팅 횟수와 어느 팀과 협업했는지", now=NOW)
    attendees = [
        {"email": "a@example.com"}, {"email": "b@example.com"},
        {"email": "unknown@example.com"}, {"email": "room@example.com", "resource": True},
        {"email": "me@example.com", "self": True},
        {"email": "declined@example.com", "responseStatus": "declined"},
    ]
    result = summarize([event("one", attendees=attendees), event("two", "물류 논의")],
                       request, teams={"a@example.com": "영업1팀", "b@example.com": "영업1팀"})
    assert result["teams"] == {"영업1팀": 1}
    assert result["team_matched_meetings"] == 1
    assert result["unmatched_people"] == 1
    answer = render(result, request)
    assert "현재" in answer and "소속" in answer
    assert "실제 참석" in answer
    assert "unknown@example.com" not in answer


def test_truncated_history_never_becomes_a_total(monkeypatch):
    from app.core import calendar_stats
    monkeypatch.setattr(google_workspace, "list_calendar_history", lambda *_a, **_k: {
        "items": [event("one")], "truncated": True})
    answer = calendar_stats.answer_statistics(object(), "올해 미팅 몇번", now=NOW)
    assert "전체" in answer and "집계하지" in answer
    assert "총 1" not in answer


def test_api_failure_never_becomes_zero_or_exposes_exception(monkeypatch):
    from app.core import calendar_stats
    def fail(*_a, **_k):
        raise RuntimeError("private-provider-details")
    monkeypatch.setattr(google_workspace, "list_calendar_history", fail)
    answer = calendar_stats.answer_statistics(object(), "올해 미팅 몇번", now=NOW)
    assert "완료하지 못" in answer
    assert "0건" not in answer and "private-provider-details" not in answer


@pytest.mark.asyncio
@pytest.mark.parametrize("excluded", ["틱톡 데일리 미팅 , 데이터분석 파트 미팅", "문서 검토 미팅"])
async def test_yearly_meeting_count_uses_january_and_bypasses_text_formatter(monkeypatch, excluded):
    """Reproduce #167: a yearly count used a future-week listing and counted four."""
    calls = []
    year = datetime.now(ZoneInfo("Asia/Seoul")).year

    class Events:
        def list(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(execute=lambda: {"items": [{
                "id": "past-meeting",
                "summary": "물류 대시보드 논의",
                "start": {"dateTime": f"{year}-01-05T10:00:00+09:00"},
                "end": {"dateTime": f"{year}-01-05T11:00:00+09:00"},
                "status": "confirmed",
            }]})

    monkeypatch.setattr(google_workspace, "build", lambda *_a, **_k: SimpleNamespace(events=Events))
    monkeypatch.setattr(gws_agent, "_get_auth_manager", lambda: SimpleNamespace(
        get_credentials=lambda email: object()))
    from app.core import llm
    monkeypatch.setattr(llm, "get_flash_client", lambda: SimpleNamespace(
        generate=lambda *_a: "OLD_FORMATTER"))

    answer = await gws_agent.GWSAgent().run(
        "내 캘린더 확인해서 이번년에 미팅 몇번있었는지 확인해줘. "
        f"(단, {excluded}은 제외해줘)",
        user_email="owner@example.com",
    )

    assert datetime.fromisoformat(calls[0]["timeMin"]).astimezone(
        ZoneInfo("Asia/Seoul")).isoformat() == f"{year}-01-01T00:00:00+09:00"
    assert "1건" in answer
    assert "OLD_FORMATTER" not in answer
    assert excluded.split(" , ")[0] in answer


@pytest.mark.parametrize("directory", ["directory_users", "ad_users"])
def test_team_lookup_uses_available_directory_and_refuses_ambiguous_matches(monkeypatch, directory):
    from app.core.calendar_stats import _lookup_teams
    from app.db import mariadb
    def fetch(sql, params=()):
        if "INFORMATION_SCHEMA" in sql:
            return [{"name": directory}]
        assert f"FROM {directory} " in sql
        assert params == ("a@example.com", "b@example.com", "a@example.com", "b@example.com")
        return [{"email": "a@example.com", "department": "영업1팀"},
                {"email": "b@example.com", "department": "영업1팀"},
                {"email": "B@example.com", "department": "영업2팀"}]
    monkeypatch.setattr(mariadb, "fetch_all", fetch)
    assert _lookup_teams({"a@example.com", "b@example.com"}) == {"a@example.com": "영업1팀"}


def test_directory_parts_roll_up_to_their_team_and_parent_only_is_unknown(monkeypatch):
    from app.core.calendar_stats import _lookup_teams
    from app.db import mariadb
    rows = [{"email": "a@example.com", "department": "Craver_Accounts > Users > 브랜드부문 > 운영본부 > 데이터 비즈니스팀 > 데이터분석파트"},
            {"email": "b@example.com", "department": "Craver_Accounts > Users > 브랜드부문 > 운영본부 > 데이터 비즈니스팀 > 사업관리파트"},
            {"email": "c@example.com", "department": "Craver_Accounts > Users > 브랜드부문 > 운영본부"}]
    monkeypatch.setattr(mariadb, "fetch_all", lambda sql, params=():
                        [{"name": "ad_users"}] if "INFORMATION_SCHEMA" in sql else rows)
    assert _lookup_teams({"a@example.com", "b@example.com", "c@example.com"}) == {
        "a@example.com": "데이터 비즈니스팀", "b@example.com": "데이터 비즈니스팀"}
