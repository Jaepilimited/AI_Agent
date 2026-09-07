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
