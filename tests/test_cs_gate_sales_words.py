# -*- coding: utf-8 -*-
"""제품명이 붙은 **판매액 조회**가 제품 Q&A 로 가던 것 (2026-09-07 실측).

cs 관문(`_CS_KEYWORDS` + `not _STRONG_DATA`)이 실사용 60일간 결정한 126건을
전부 읽었다 — **대부분 맞다** (센텔라 앰플 특징·성분·사용법…). 틀린 무리가
하나 있었고 원인은 같았다:

    `_STRONG_DATA` 에 `매출`·`수량`·`광고비` 는 있는데 **`판매액` 이 없다.**

    "센텔라테카 앰플 50ML, 히알루테카 앰플 … 판매액알려줘"   → cs
    "오늘 기준으로 확인해줘 판매액 오로라 미스트 …"          → cs
    "라인이 랩인네이처인 제품들 제품별 성장성 매트릭스 보여줘"   → cs

⚠️ `판매` 를 통째로 넣지 않는다 — "이 제품 판매 중단됐나요?" 같은 진짜 CS 문의가
   조회로 샌다. 재는 말(`판매액`·`판매량`)만 넣는다.
"""

import pytest

from app.agents.orchestrator import OrchestratorAgent


@pytest.fixture(scope="module")
def route():
    agent = OrchestratorAgent.__new__(OrchestratorAgent)

    def _route(question: str):
        return OrchestratorAgent._keyword_classify_ex(agent, question)

    return _route


SALES = [
    "센텔라테카 앰플 50ML, 히알루테카 앰플, 50mL 아젤라익 앰플 30mL 판매액알려줘",
    "오늘 기준으로 확인해줘 판매액 오로라 미스트 아젤라익 앰플 30mL 센텔라 테카 앰플",
    "센텔라 앰플 판매량 알려줘",
    "라인이 랩인네이처인 제품들 제품별 성장성 매트릭스 보여줘",
]


@pytest.mark.parametrize("question", SALES)
def test_판매액을_묻는_질문은_조회다(question, route):
    got, _ = route(question)
    assert got == "bigquery", f"{question!r} → {got} (제품 Q&A 로 샜다)"


# ── 반대 방향: 진짜 제품 문의는 그대로 cs ─────────────────────────────────
CS = [
    "센텔라 앰플의 특징을 알려줘.",
    "센텔라 앰플 사용법과 주요 성분을 알려줘",
    "마다가스카르 센텔라 앰플 폼 전성분 알려줘",
    "센텔라 앰플 용기는 플라스틱인가요?",
    # ⚠️ 아래 한 줄만 실사용 기록이 아니라 **가드용으로 지어낸** 문장이다.
    #    `판매` 를 통째로 넣으면 이런 문의가 조회로 새기 때문에 넣어 둔다.
    "센텔라 앰플 판매 중단됐나요?",
    "선크림은 몇시간마다 도포해야하나요?",
    "히알루-시카 젠틀 클렌징 밀크 특징은?",
    "지성 피부에 추천할 상품?",
]


@pytest.mark.parametrize("question", CS)
def test_제품_문의는_그대로_제품_QA다(question, route):
    got, confident = route(question)
    assert got == "cs", f"{question!r} → {got}"
    assert confident
