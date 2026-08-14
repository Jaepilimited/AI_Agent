# -*- coding: utf-8 -*-
"""회사 밖 사실을 그라운딩 없이 답해 지어내던 것을 지킨다.

사고 원문 (2026-08-11 제보): "리센느 멤버는 몇명임?" 에
**"우연, 이한, 벨라, 케이티, 민주"** 라고 답했다 — 전부 지어낸 이름이다
(실제는 원이·리브·미나미·메이·제나). 같은 대화에서 같은 주제가 세 번 다르게
나갔다: ① 환각 ② "연동돼 있지 않습니다" ③ 검색이 걸려 정답 + 스스로 정정.
제보자가 "어떤 로직으로 답을 가져오는지 모르겠다"고 쓴 것이 그 불일치다.

⛔ 이 테스트는 **_SEARCH_KEYWORDS 에 고유명사를 쌓는 방식으로 통과시키면 안 된다.**
   목록에 없는 이름에서 그대로 재발한다. 판정은 질문의 구조로 한다.
"""
import pytest

from app.agents.orchestrator import OrchestratorAgent


@pytest.fixture(scope="module")
def orc():
    return OrchestratorAgent.__new__(OrchestratorAgent)


class TestGroundsExternalFacts:
    """모델 기억으로 답하면 지어내는 질문 — 반드시 검색을 태운다."""

    @pytest.mark.parametrize("q", [
        "리센느 멤버는 몇명임?",          # 제보 원문
        "아이브 멤버 누구야",
        "뉴진스 데뷔일 언제야",
        "손흥민 나이 몇살이야",
        "그 감독 본명이 뭐야",
    ])
    def test_search_required(self, orc, q):
        assert orc._needs_web_search(q) is True

    def test_not_keyword_based(self, orc):
        """목록에 없는 고유명사에도 걸려야 한다 — 사전을 쌓는 방식이 아니다."""
        assert not any(k in "제나벨루가" for k in orc._SEARCH_KEYWORDS)
        assert orc._needs_web_search("제나벨루가 멤버 몇명이야") is True


class TestDoesNotGroundInternal:
    """⚠️ 넓히면 사내 질문까지 검색을 타 느려진다. 양방향을 함께 지킨다."""

    @pytest.mark.parametrize("q", [
        "보고서 기능은 어떤 때 쓰면 좋아?",   # 자기 기능
        "너는 뭐 할 수 있어?",
        "안녕?",
        "7월 매출 얼마야",                 # 사내 데이터
        "우리 회사 거래처 몇 곳이야",
        "인도네시아 쇼피 매출 얼마",
    ])
    def test_no_search(self, orc, q):
        assert orc._needs_web_search(q) is False


class TestExistingBehaviourKept:
    """2026-08-13 에 고친 것 — 시간어만 걸린 인사말은 계속 검색하지 않는다."""

    def test_greeting_with_time_word(self, orc):
        assert orc._needs_web_search("안녕? 오늘 뭐 도와줄 수 있어?") is False

    def test_real_time_topic_still_searches(self, orc):
        assert orc._needs_web_search("오늘 환율 얼마야") is True
