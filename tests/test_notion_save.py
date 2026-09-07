# -*- coding: utf-8 -*-
"""노션 저장 관문 회귀 — 저장과 검색을 **양방향**으로 지킨다."""
import pytest

from app.core import notion_save as ns


@pytest.mark.parametrize("query", [
    "이 답변 노션에 넣어줘",
    "방금 그거 노션에 저장해줘",
    "노션에 올려줘",
    "위 내용 노션 페이지에 추가해줘",
    "브리핑 매일 노션에 넣어줘",
])
def test_save_intent_true(query):
    assert ns.notion_save_intent(query) is True


@pytest.mark.parametrize("query", [
    "노션에서 휴가 규정 찾아줘",
    "노션에 뭐 있어?",
    "노션 문서 어디 있어",
    "노션 정리 잘 돼 있나?",
    "2026년 일본 매출 알려줘",
    "잔디로 보내줘",
])
def test_save_intent_false(query):
    """⛔ 검색을 가로채면 사내 문서 검색이 통째로 죽는다."""
    assert ns.notion_save_intent(query) is False


def test_extract_url_finds_the_notion_link():
    text = "https://www.notion.so/내-페이지-24f1a2b3c4d54e6f8a9b0c1d2e3f4a5b 여기에"
    assert ns.extract_url(text).startswith("https://www.notion.so/")


def test_extract_url_returns_empty_without_a_link():
    assert ns.extract_url("그냥 아무 말") == ""


def _msgs(*pairs):
    return [{"role": role, "content": text} for role, text in pairs]


def test_prompt_asks_for_url_and_explains_the_connection():
    prompt = ns.build_prompt()
    assert "URL" in prompt or "주소" in prompt
    assert "연결" in prompt          # ⛔ 404 의 실제 원인을 함께 알려준다
    assert ns._MARKER.search(prompt)


def test_pending_reads_the_marker_from_the_previous_assistant():
    messages = _msgs(
        ("user", "이 답변 노션에 넣어줘"),
        ("assistant", ns.build_prompt()),
        ("user", "https://www.notion.so/x"),
    )
    assert ns.pending(messages)["kind"] == "답변"


def test_pending_is_none_when_the_last_assistant_has_no_marker():
    """⛔ 오래된 요청이 나중의 일반 대화를 가로채면 안 된다."""
    messages = _msgs(
        ("user", "이 답변 노션에 넣어줘"),
        ("assistant", ns.build_prompt()),
        ("user", "아니 됐고 매출 알려줘"),
        ("assistant", "2026년 매출은 …"),
        ("user", "고마워"),
    )
    assert ns.pending(messages) is None


def test_target_answer_is_the_message_before_the_question():
    messages = _msgs(
        ("user", "2026년 일본 매출은?"),
        ("assistant", "일본 매출은 55.1억원입니다."),
        ("user", "이거 노션에 넣어줘"),
        ("assistant", ns.build_prompt()),
        ("user", "https://www.notion.so/x"),
    )
    assert ns.target_answer(messages) == "일본 매출은 55.1억원입니다."


def test_target_answer_empty_when_history_is_trimmed():
    """⛔ 못 찾으면 빈 문자열이다 — 엉뚱한 것을 저장하지 않는다."""
    messages = _msgs(
        ("user", "이거 노션에 넣어줘"),
        ("assistant", ns.build_prompt()),
        ("user", "https://www.notion.so/x"),
    )
    assert ns.target_answer(messages) == ""
