import base64
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.agents import gws_agent
from app.core import google_workspace


SEOUL = ZoneInfo("Asia/Seoul")
NOW = datetime(2026, 8, 13, 15, 30, tzinfo=SEOUL)


def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii")


def test_build_gmail_query_converts_today_to_date_range():
    assert gws_agent.build_gmail_query("오늘 메일 요약", now=NOW) == (
        "after:2026/08/13 before:2026/08/14"
    )


def test_build_gmail_query_converts_yesterday_received_mail():
    assert gws_agent.build_gmail_query("어제 받은 메일 내용 요약해줘", now=NOW) == (
        "after:2026/08/12 before:2026/08/13 -from:me"
    )


def test_build_gmail_query_uses_only_current_question_from_context():
    contextualized = (
        "[이전 대화]\n지난달 매출 보고서를 찾아줘\n\n"
        "[현재 질문]\n오늘 메일 요약"
    )
    assert gws_agent.build_gmail_query(contextualized, now=NOW) == (
        "after:2026/08/13 before:2026/08/14"
    )


def test_build_gmail_query_handles_last_month_and_common_received_wording():
    assert gws_agent.build_gmail_query("지난달 들어온 메일 정리", now=NOW) == (
        "after:2026/07/01 before:2026/08/01 -from:me"
    )


def test_build_gmail_query_handles_recent_unread_mail():
    assert gws_agent.build_gmail_query("최근 안 읽은 메일 보여줘", now=NOW) == (
        "newer_than:7d is:unread"
    )


def test_build_gmail_query_drops_colloquial_what_is_it_suffix():
    assert gws_agent.build_gmail_query("최신메일이머야", now=NOW) == ""
    assert gws_agent.build_gmail_query("최신 메일이 뭐야?", now=NOW) == ""


def test_build_gmail_query_keeps_today_filter_with_colloquial_suffix():
    assert gws_agent.build_gmail_query("오늘 메일머야", now=NOW) == (
        "after:2026/08/13 before:2026/08/14"
    )


def test_build_gmail_query_understands_just_arrived_mail():
    assert gws_agent.build_gmail_query("방금 온 메일 뭐야", now=NOW) == (
        "newer_than:1d -from:me"
    )


def test_latest_mail_request_fetches_only_the_newest_message():
    assert gws_agent.gmail_result_limit("최신메일이머야") == 1
    assert gws_agent.gmail_result_limit("오늘 메일머야") == 10


def test_today_mail_is_classified_as_gmail_not_all_tools():
    assert gws_agent.GWSAgent._classify_tool("오늘 메일 요약") == "gmail"
    assert gws_agent.GWSAgent._classify_tool("오늘 일정") == "calendar"
    assert gws_agent.GWSAgent._classify_tool("오늘 메일과 일정") == "all"


def test_tool_classification_ignores_previous_conversation():
    query = "[이전 대화]\n최근 메일 요약\n\n[현재 질문]\n오늘 일정"
    assert gws_agent.GWSAgent._classify_tool(query) == "calendar"


def test_extract_gmail_body_prefers_plain_text_over_duplicate_html():
    payload = {
        "mimeType": "multipart/alternative",
        "parts": [
            {
                "mimeType": "text/plain",
                "body": {"data": _b64("안녕하세요.\n실제 메일 본문입니다.")},
            },
            {
                "mimeType": "text/html",
                "body": {"data": _b64("<p>중복 HTML 본문입니다.</p>")},
            },
        ],
    }

    assert google_workspace.extract_gmail_body(payload) == (
        "안녕하세요.\n실제 메일 본문입니다."
    )


def test_extract_gmail_body_uses_readable_html_fallback_and_skips_attachment():
    payload = {
        "mimeType": "multipart/mixed",
        "parts": [
            {
                "mimeType": "text/html",
                "body": {"data": _b64("<p>첫 줄<br>둘째 줄 &amp; 내용</p>")},
            },
            {
                "mimeType": "text/plain",
                "filename": "secret.txt",
                "body": {"data": _b64("첨부파일 내용은 읽지 않는다")},
            },
        ],
    }

    body = google_workspace.extract_gmail_body(payload)
    assert "첫 줄" in body
    assert "둘째 줄 & 내용" in body
    assert "첨부파일" not in body


def test_agent_collect_uses_compiled_query_and_full_body(monkeypatch):
    captured = {}

    def fake_search(_creds, query, max_results):
        captured.update(query=query, max_results=max_results)
        return [{
            "subject": "테스트 메일",
            "from": "sender@example.com",
            "date": "Thu, 13 Aug 2026 09:00:00 +0900",
            "snippet": "짧은 미리보기",
            "body": "미리보기에 없던 실제 본문 내용",
        }]

    monkeypatch.setattr(gws_agent, "search_gmail", fake_search)
    result = gws_agent.GWSAgent()._collect(object(), "오늘 메일 요약", "gmail")

    assert captured == {
        "query": gws_agent.build_gmail_query("오늘 메일 요약"),
        "max_results": 10,
    }
    assert "미리보기에 없던 실제 본문 내용" in result
    assert "짧은 미리보기" not in result


def test_search_gmail_requests_full_messages_and_returns_body(monkeypatch):
    class Request:
        def __init__(self, value):
            self.value = value

        def execute(self):
            return self.value

    class Messages:
        def list(self, **_kwargs):
            return Request({"messages": [{"id": "message-1"}]})

        def get(self, **kwargs):
            assert kwargs["format"] == "full"
            return Request({
                "snippet": "미리보기",
                "payload": {
                    "mimeType": "text/plain",
                    "headers": [
                        {"name": "Subject", "value": "본문 테스트"},
                        {"name": "From", "value": "sender@example.com"},
                        {"name": "Date", "value": "2026-08-13"},
                    ],
                    "body": {"data": _b64("검색 결과에 포함될 실제 본문")},
                },
            })

    class Users:
        def __init__(self):
            self._messages = Messages()

        def messages(self):
            return self._messages

    class Service:
        def users(self):
            return Users()

    monkeypatch.setattr(google_workspace, "build", lambda *_args, **_kwargs: Service())

    result = google_workspace.search_gmail(object(), "after:2026/08/13", max_results=1)

    assert result[0]["subject"] == "본문 테스트"
    assert result[0]["body"] == "검색 결과에 포함될 실제 본문"


@pytest.mark.asyncio
async def test_missing_gws_token_uses_authenticated_relative_login_route(monkeypatch):
    """A missing token directs the browser to the JWT-bound login endpoint only."""
    class MissingAuth:
        def get_credentials(self, _email):
            return None

    monkeypatch.setattr(gws_agent, "_get_auth_manager", lambda: MissingAuth())
    answer = await gws_agent.GWSAgent().run("오늘 일정", user_email="owner@example.com")

    assert "<!-- gws-auth:/auth/google/login -->" in answer
    assert "owner@example.com" not in answer
    assert "accounts.google.com" not in answer
