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
    """구어체 꼬리는 떼되, **질의를 통째로 비우지는 않는다.**

    ⛔ 이 테스트는 원래 결과가 `""` 이기를 기대했다. 그런데 빈 질의는 아예
       보내지지 않아 **언제나 0건**이고, 그 0건은 "메일이 없다" 와 똑같이 보인다.
       실제로 프로덕션에서 `최신메일이머야`(2026-08-13)가 "검색 결과가 없습니다"
       로 끝났다. `최신` 은 최근성 표현이므로 연산자로 번역돼야 한다
       (2026-09-07 실측 후 기대값 정정 — 같은 질문이 이제 10건을 돌려준다).
    """
    for question in ("최신메일이머야", "최신 메일이 뭐야?"):
        got = gws_agent.build_gmail_query(question, now=NOW)
        assert got == "newer_than:7d", got
        # 구어체 꼬리가 검색어로 남지 않는다 (이 테스트의 원래 목적)
        assert not [t for t in got.split() if ":" not in t], got


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
    # ⚠️ `_collect` 는 (결과, 공지) 를 돌려준다 — 넓혀 찾은 사실을 결과 텍스트에만
    #    적으면 정리 LLM 이 지운다 (붐따 #148)
    result, notices = gws_agent.GWSAgent()._collect(object(), "오늘 메일 요약", "gmail")

    assert captured == {
        "query": gws_agent.build_gmail_query("오늘 메일 요약"),
        "max_results": 10,
    }
    assert "미리보기에 없던 실제 본문 내용" in result
    assert "짧은 미리보기" not in result
    assert notices == [], "정확히 맞은 검색을 넓혔다고 말하면 안 된다"


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


# ── 「OO님이 보낸 메일」이 내 보낸편지함을 뒤졌다 (2026-09-09 붐따 스윕) ────────
#
#     "이해인님이 보낸 메일 찾아줘"  ->  in:sent from:이해인님
#
# 내 보낸편지함에는 **내가 보낸 것만** 있으므로 이 조합은 구조적으로 항상 0건이다.
# 그래서 좁혀찾기 사다리가 `from:` 을 떼어 **내가 보낸 메일 12건**을 답으로 내놨다.
# 넓혔다고 밝히긴 했지만, 밝힌 그 내용이 이미 다른 질문이다
# (드라이브 규칙과 같다: 잡음은 답처럼 보여서 0건보다 나쁘다).


def test_a_named_sender_is_not_my_sent_folder():
    """⛔ 이 건 자체 — 보낸사람이 지목되면 `in:sent` 가 아니다."""
    q = gws_agent.build_gmail_query("이해인님이 보낸 메일 찾아줘")
    assert "from:이해인" in q
    assert "in:sent" not in q


def test_the_honorific_is_stripped_from_the_sender():
    """⚠️ `from:이해인님` 은 Gmail 이 못 찾는다."""
    assert "from:이해인님" not in gws_agent.build_gmail_query("이해인님이 보낸 메일 찾아줘")
    assert "from:haein" in gws_agent.build_gmail_query("haein 님이 보낸 메일")


def test_a_short_name_keeps_its_last_character():
    """⚠️ 두 글자 미만으로 줄면 떼지 않는다 (조사 규칙과 같다)."""
    assert gws_agent._strip_honorific("이해인님") == "이해인"
    assert gws_agent._strip_honorific("하님") == "하님"


def test_other_sending_verbs_are_understood():
    """⚠️ 동사가 `보낸` 하나뿐이면 「발송한」이 `in:sent` 로 샌다."""
    q = gws_agent.build_gmail_query("대표님이 발송한 메일 찾아줘")
    assert "from:대표" in q and "in:sent" not in q


def test_my_own_sent_mail_still_uses_in_sent():
    """정상 경로는 그대로다 — 좁히다가 쓸모를 없애면 안 된다."""
    for q in ("내가 보낸 메일 찾아줘", "제가 보낸 메일 알려줘"):
        assert "in:sent" in gws_agent.build_gmail_query(q)
        assert "from:" not in gws_agent.build_gmail_query(q)


def test_the_sender_name_does_not_leak_back_as_a_keyword():
    """⚠️ 이름을 `from:` 으로 뺐으면 그 이름이 붙은 조각도 검색어가 아니다.

    실측: `이해인님으로부터 온 메일` → `from:이해인 이해인님으로` 가 되어
    본문에 그 글자가 없으면 AND 로 걸려 0건이 됐다.
    """
    q = gws_agent.build_gmail_query("이해인님으로부터 온 메일")
    assert "from:이해인" in q
    assert "이해인님으로" not in q
    assert "님이" not in gws_agent.build_gmail_query("haein 님이 보낸 메일")


def test_a_real_keyword_survives_next_to_a_sender():
    q = gws_agent.build_gmail_query("이해인님이 보낸 물류 관련 메일")
    assert "from:이해인" in q and "물류" in q
