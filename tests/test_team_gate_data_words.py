# -*- coding: utf-8 -*-
"""팀 낱말이 데이터 질문을 사내 문서로 끌고 가던 것 (2026-09-07 실측).

`if has_team and not has_data: return ("notion", True)` 관문을 실사용 60일치로
전수 확인했다 — **32건 중 28건은 맞다** (앨리비 절차·휴가 규정·출장 가이드…).
그래서 확신을 빼지 않았다. 틀린 4건은 원인이 하나였다:

    `_DATA_OVERRIDE` 에 `판매`·`프로모션`·`광고` 가 없어서 팀 낱말이 이겼다.

이 목록의 목적이 바로 「데이터 낱말이면 팀 낱말을 이긴다」이고 `매출`·`광고비`·
`리뷰` 는 이미 들어 있다 — 빠진 것을 채운 것이지 예외를 늘린 것이 아니다.

⚠️ 안전한 이유: 이 관문 **바로 다음**이 `_DOC_WORD` 관문이라, 데이터 낱말이 붙어도
   「가이드라인·절차·정책」이 있으면 그쪽에서 notion 으로 잡힌다. 아래 양방향 검사가
   그 성질을 고정한다.
"""

import pytest

from app.agents.orchestrator import OrchestratorAgent


@pytest.fixture(scope="module")
def route():
    agent = OrchestratorAgent.__new__(OrchestratorAgent)

    def _route(question: str):
        return OrchestratorAgent._keyword_classify_ex(agent, question)

    return _route


DATA_QUESTIONS = [
    "b2c west 판매에서, 북미 내 동부 서부 중 어디가 판매액이 높아?",
    "b2c west 판매에서, 북미 중 어떤 나라에서 가장 판매액이 높아?",
    "9월 jbt 프로모션 뭐 있음? 그리고 메가와리도 등록되어 있는지 확인해줘.",
    "메타 최근 한달간 west 권역에서 광고 소재 평균 수 top5 브랜드 알려줘",
]


@pytest.mark.parametrize("question", DATA_QUESTIONS)
def test_팀낱말이_데이터_질문을_가로채지_않는다(question, route):
    got, _ = route(question)
    assert got == "bigquery", f"{question!r} → {got} (사내 문서로 샜다)"


# 같은 낱말이 들어가도 **문서를 달라는 모양**이면 그대로 문서다
DOC_QUESTIONS = [
    "B2B2팀 광고 심의 가이드라인 알려줘",
    "프로모션 진행 절차 알려줘",
    "판매 반품 정책 문서 찾아줘",
    "휴가 신청 절차 알려줘",
    "GM팀 출장 가이드 어디 있어?",
    "신규 거래처(바이어) ERP 등록 절차 알려줘",
    "B2B 티어별 가격표는 어디서 볼 수 있나요?",
    "앨리비 계약서 검토 의뢰 절차 노션 문서 찾아줘",
    "출장 보고서와 정산서 작성 시 B2B2팀의 내부 역할 분담 규정은 어떻게 되나요?",
]


@pytest.mark.parametrize("question", DOC_QUESTIONS)
def test_문서_질문은_그대로_문서다(question, route):
    got, confident = route(question)
    assert (got, confident) == ("notion", True), f"{question!r} → {got}/{confident}"


def test_자기_기능_질문에서_이_프로그램도_우리를_가리킨다(route):
    """`이 시스템`·`이 서비스`·`이 앱` 은 있는데 `이 프로그램` 만 빠져 있었다."""
    got, _ = route("이 프로그램 전체 가이드는 없어?")
    assert got == "direct", got


def test_다른_툴_사용법은_여전히_문서다(route):
    for q in ("노션 사용법 알려줘", "틱톡샵 접속 방법 알려줘"):
        got, _ = route(q)
        assert got == "notion", f"{q!r} → {got}"
