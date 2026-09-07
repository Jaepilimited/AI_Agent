# -*- coding: utf-8 -*-
"""직전 답변을 부정하는 발화 판정 — 실사용 60일 표본으로 고정.

⛔ 문장 **중간**의 `말고` 는 부정이 아니다. 실측 26건이 전부 정상적인 좁혀
   묻기였다("상위 10개 말고 전체로 줘"). 여기 걸리면 멀쩡한 대화가 끊긴다.
"""

import pytest

from app.core.route_intent import rejection_kind


# 실사용에서 그대로 가져온 것들 (2026-09-07, 첫머리 부정 20건 중)
INFORMATIVE = [
    "아니 ;; 내 개인 업무 노션 ;;",
    "아니 ㅠㅠㅠㅠㅠㅠ!!!!! 나 개인 업무 노션 페이지 구성할건데 제안해달라고!!!!",
    "아니 톤브라이트닝 미스트 월별 매출액 및 판매량",
    "아니 라인이 랩인네이처인 제품들 제품별 성장성 매트릭스 보여줘",
    "아니 ㅡㅡ 주간보고를 작성해달라고",
    "아니 너가 되게 해보라고 !!!!!!!!!!",
    "뭔 소리야 틀렸음; 환각을 정답처럼 말하지 마셈",
    "뭔 소리하는거야. 리센느 멤버 5명을 내부 데이터베이스에서 가져왔다고?",
]

BARE = [
    "아니...",
    "아니 ;;",
]

# 부정이 아니다 — 정상적인 좁혀 묻기
NONE = [
    "상위 10개 말고 25년도 출시된 품목 전체로 줘",
    "막대표 말고 각각 국가별 비중값 볼 수 있게 원형 그래프로 줄 수 있어?",
    "오 좋아 근데 분리만 다시해보자. 앰플, 크림, 토너 각각 따로",
    "기타국가는 뭐야? 누적 매출 비중이 가장 큰 개별 국가부터 보여주고",
    "2026년 8월 일본 매출 알려줘",
    "센텔라 앰플 특징 알려줘",
    "",
]


@pytest.mark.parametrize("text", INFORMATIVE)
def test_부정하면서_고쳐_말한_것은_informative(text):
    assert rejection_kind(text) == "informative", text


@pytest.mark.parametrize("text", BARE)
def test_부정만_하고_정보가_없으면_bare(text):
    assert rejection_kind(text) == "bare", text


@pytest.mark.parametrize("text", NONE)
def test_좁혀_묻기는_부정이_아니다(text):
    assert rejection_kind(text) == "none", text


from app.core.route_intent import SOURCE_GATE_PROMPT, parse_gate


@pytest.mark.parametrize("raw,want", [
    ("SELF", "SELF"),
    ("self", "SELF"),
    ("  SELF  ", "SELF"),
    ("SELF — 만들어 달라는 요청입니다", "SELF"),
    ("NEED", "NEED"),
    ("need", "NEED"),
])
def test_판정_문자열을_읽는다(raw, want):
    assert parse_gate(raw) == want


@pytest.mark.parametrize("raw", ["", None, "   ", "글쎄요", "NEED 또는 SELF"])
def test_모르면_뒤지는_쪽이다(raw):
    """⛔ 사내 데이터 우선 — 읽을 수 없으면 NEED 다. 안전한 쪽 실패."""
    assert parse_gate(raw) == "NEED"


def test_프롬프트가_사내데이터_우선을_못박는다():
    """이 줄이 「애매하면 뒤진다」를 보증하는 유일한 자리다."""
    assert "애매하면 NEED" in SOURCE_GATE_PROMPT
    assert "SELF" in SOURCE_GATE_PROMPT and "NEED" in SOURCE_GATE_PROMPT


def test_경계_4자까지는_정보가_없는_것으로_본다():
    """스펙이 「4자 이하」로 못박은 계약 — 경계가 움직이면 되묻기 범위가 달라진다.

    ⚠️ 실사용 표본이 아니라 **명시된 임계값**을 고정하는 테스트다.
    """
    assert rejection_kind("아니 가나다라") == "bare"        # 남는 글자 4
    assert rejection_kind("아니 가나다라마") == "informative"  # 남는 글자 5


from app.core.route_intent import clarify_message


def test_되묻는_문장은_직전에_무엇을_했는지_밝힌다():
    """무엇을 고쳐 말해야 할지 알려면 직전 경로를 알아야 한다."""
    msg = clarify_message("notion")
    assert "사내 문서" in msg
    assert "?" in msg or "까요" in msg


def test_직전_경로를_모르면_되묻지_않는다():
    """고칠 대상이 없다 — 그냥 정상 라우팅한다."""
    assert clarify_message("") == ""
    assert clarify_message(None) == ""


@pytest.mark.parametrize("route,label", [
    ("bigquery", "사내 데이터"),
    ("notion", "사내 문서"),
    ("cs", "제품 Q&A"),
    ("gws", "메일"),
])
def test_경로마다_사람이_읽는_이름이_있다(route, label):
    assert label in clarify_message(route)


def test_되묻기가_두_경로에_모두_걸려_있다():
    """⛔ 한쪽만 달면 스트리밍이냐에 따라 답이 갈린다 (이 저장소의 단골 사고)."""
    src = open("app/agents/orchestrator.py", encoding="utf-8").read()
    assert src.count("clarify_asked_bare_rejection") == 2, "두 경로에 걸려야 한다"
    assert 'path="route_and_execute"' in src
    assert 'path="route_and_stream"' in src
