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
