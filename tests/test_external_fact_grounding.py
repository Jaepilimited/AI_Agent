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


# ── 존재를 묻는 질문 (2026-08-26 사용자 제보) ────────────────────────────────

@pytest.mark.parametrize("q", [
    "강남역에 무지개쇼핑센터가 있어?",
    "판교에 현대백화점 있나요?",
    "부산에 신세계 센텀시티 있어?",
])
def test_existence_questions_about_the_outside_world_are_grounded(q):
    """⛔ 실제 제보: "강남역에 무지개쇼핑센터가 있어?" 에 **"확인할 수 없습니다"** 로
       답했다. 지어내지는 않았지만 **확인할 수 있는 것을 확인하지 않고 되물었다.**

    검색을 태우면 5.1초에 정답이 나온다 (대한무지개종합상가 · 서초구 사임당로 151 ·
    강남역 5번 출구 도보 10분). `~있어?` 형태가 사실 질문 판정에 빠져 있었다.
    """
    agent = OrchestratorAgent.__new__(OrchestratorAgent)
    assert agent._needs_web_search(q), q


@pytest.mark.parametrize("q", [
    "다른 방법 있어?",          # 앞 대화를 가리킨다
    "혹시 예시 있어?",
    "또 있어?",
    "설명해줄 수 있어?",         # 존재가 아니라 요청이다
    "도와줄 수 있어?",
    "그런 기능 있나?",           # 어시스턴트 자신
    "센텔라 앰플 재고 있어?",     # 사내 데이터
    "매출 테이블에 원가 컬럼 있나요?",
])
def test_existence_wording_alone_does_not_trigger_search(q):
    """⚠️ `있어` 는 흔한 말이다. 전부 검색하면 **5초를 버리고 엉뚱한 웹 문서를
       근거로** 끌어온다 — 느린 것보다 그쪽이 나쁘다.

    막는 축은 셋이다: 앞 대화를 가리키는 말 · `~할 수 있어`(요청) · 사내/자기 어휘.
    """
    agent = OrchestratorAgent.__new__(OrchestratorAgent)
    assert not agent._needs_web_search(q), q


def test_business_vocabulary_has_no_bare_question_endings():
    """⛔ `_BIZ_CONTEXT` 에 **"있나요"·"존재"** 가 들어 있었다 (2026-08-26 제거).

    업무 어휘가 아니라 의문 어미다. 이것 하나 때문에 "판교에 현대백화점 있나요?" 가
    **사내 질문으로 분류돼** 검색 그라운딩이 막혔다. `라인` ⊂ `가이드라인` 과 같은 부류 —
    짧고 흔한 말을 포함 검사에 그대로 두면 뜻이 뒤집힌다.
    """
    for w in ("있나요", "있어", "존재", "있습니까"):
        assert w not in OrchestratorAgent._BIZ_CONTEXT, w
        assert w not in OrchestratorAgent._DATA_KEYWORDS, w
