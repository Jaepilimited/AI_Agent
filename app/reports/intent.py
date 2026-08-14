# -*- coding: utf-8 -*-
"""질문 유형 판정 — 보고서의 **뼈대**를 질문에 맞춘다.

⛔ 예전엔 플래너 프롬프트에 *"보통 좋은 순서: 총량 → 추세 → 구성 → 전년비 → 순위"* 가
   박혀 있었다. 그 한 줄 때문에 **무엇을 물어도 비슷하게 생긴 보고서**가 나왔다
   (2026-08-14 사용자 지적). "왜 줄었나"와 "어디에 집중할까"는 답의 모양이 달라야 한다.

판정은 **규칙**이 한다 (LLM 아님). 뼈대는 프롬프트에 넣어 LLM 이 지표·축만 채우게 하고,
LLM 이 죽어도 같은 뼈대로 기본 계획을 만든다 — 이 파이프라인의 다른 곳과 같은 방식이다.

⚠️ 뼈대는 **강제가 아니라 우선순위**다. 질문이 특이하면 LLM 이 다른 블록을 골라도 되고,
   `_clean_section` 이 어휘 검사를 그대로 한다.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple

# 유형 → (판정 정규식, 뼈대 설명, 권장 블록 순서)
# 순서가 곧 보고서의 모양이다. 앞에 오는 절이 그 유형의 논지를 만든다.
INTENTS: List[Tuple[str, re.Pattern, str, List[str]]] = [
    ("change", re.compile(
        r"왜|원인|요인|이유|변화|변동|증감|늘었|줄었|급증|급감|떨어|빠졌|성장|하락|둔화"),
     "무엇이 변화를 만들었는지부터 말한다. 총량은 뒤로 미룬다.",
     ["compare", "contribution", "movers", "trend", "promotion", "breakdown", "total"]),

    ("efficiency", re.compile(
        r"효율|수익성|원가율|할인율|마진|roas|전환율|객단가|비율|per\b|당\s*단가|대비\s*효과"),
     "비율이 논지다. 규모는 비율을 읽기 위한 배경으로만 둔다.",
     ["ratio", "correlation", "breakdown", "compare", "trend", "total"]),

    ("compare_target", re.compile(
        r"비교|대비|보다|차이|다른\s*(곳|나라|국가|팀|채널)|어디가\s*(더|가장)|누가\s*더|"
        r"vs\b|특별|남다"),
     "대상과 나머지를 나란히 놓는 것이 논지다.",
     ["versus", "compare", "ranking", "breakdown", "trend", "total"]),

    ("concentration", re.compile(
        r"집중|쏠림|의존|편중|몰려|비중이\s*(큰|높은)|상위\s*몇|파레토|리스크"),
     "어디에 몰려 있고 그게 위험한지가 논지다.",
     ["concentration", "breakdown", "ranking", "mixshift", "compare", "total"]),

    ("timing", re.compile(
        r"언제|시기|주기|계절|성수기|비수기|월별\s*패턴|행사|프로모션|메가와리|캠페인\s*시점"),
     "시점이 논지다. 달마다 왜 다른지를 일정과 맞춰 본다.",
     ["seasonality", "promotion", "trend", "compare", "breakdown", "total"]),

    ("ranking", re.compile(
        r"top\s*\d|상위|순위|랭킹|best|잘\s*팔리|많이\s*팔|1위|베스트"),
     "무엇이 앞에 있는지가 논지다.",
     ["ranking", "breakdown", "compare", "movers", "trend", "total"]),

    ("size", re.compile(r"얼마|규모|총|합계|매출은|현황|알려|정리"),
     "규모와 방향을 차례로 말한다.",
     ["total", "trend", "breakdown", "compare", "ranking"]),
]

DEFAULT = ("size", "규모와 방향을 차례로 말한다.",
           ["total", "trend", "breakdown", "compare", "ranking"])


def detect(question: str) -> Dict[str, Any]:
    """질문 유형과 뼈대. 여러 개 걸리면 **앞선 유형이 이긴다** (더 구체적인 것부터 배열).

    걸리는 게 없으면 규모형이 기본이다 — "일본 매출 보고서" 같은 질문이 여기 온다.
    """
    q = (question or "").lower()
    hits = [(name, desc, order) for name, pat, desc, order in INTENTS if pat.search(q)]
    if not hits:
        name, desc, order = DEFAULT
        return {"intent": name, "also": [], "shape": desc, "order": order}
    name, desc, order = hits[0]
    return {"intent": name, "also": [h[0] for h in hits[1:3]], "shape": desc, "order": order}


def skeleton_text(det: Dict[str, Any]) -> str:
    """플래너 프롬프트에 넣을 뼈대 안내."""
    also = f" (부수적으로 {', '.join(det['also'])} 성격도 있음)" if det.get("also") else ""
    return (f"## 이 질문의 유형: **{det['intent']}**{also}\n"
            f"{det['shape']}\n"
            f"권장 절 순서: {' → '.join(det['order'])}\n"
            f"⚠️ 이 순서는 **우선순위**다. 질문이 요구하면 다른 블록을 써도 되지만, "
            f"맨 앞 두 절은 이 유형의 논지를 만드는 것으로 두세요.")


# 유형별 기본 계획 — LLM 이 죽어도 **유형에 맞는 모양**은 나온다
_FALLBACK_SECTIONS: Dict[str, List[Dict[str, Any]]] = {
    "change": [
        {"block": "compare", "metric": "매출", "dim": "국가", "title": "국가별 전년 대비"},
        {"block": "contribution", "metric": "매출", "dim": "국가", "title": "증감 기여도"},
        {"block": "movers", "metric": "매출", "dim": "채널", "title": "크게 변한 채널"},
        {"block": "trend", "metric": "매출", "dim": "월", "title": "월별 추세"},
        {"block": "total", "metric": "매출", "title": "기간 총량"},
    ],
    "efficiency": [
        {"block": "ratio", "metric": "할인", "metric2": "매출", "dim": "채널",
         "title": "채널별 할인율"},
        {"block": "breakdown", "metric": "매출", "dim": "채널", "title": "채널별 규모"},
        {"block": "compare", "metric": "매출", "dim": "채널", "title": "채널별 전년 대비"},
        {"block": "total", "metric": "매출", "title": "기간 총량"},
    ],
    "compare_target": [
        {"block": "versus", "metric": "매출", "title": "대상과 나머지"},
        {"block": "compare", "metric": "매출", "dim": "국가", "title": "국가별 전년 대비"},
        {"block": "ranking", "metric": "매출", "dim": "제품", "limit": 15, "title": "제품 상위"},
        {"block": "trend", "metric": "매출", "dim": "월", "title": "월별 추세"},
    ],
    "concentration": [
        {"block": "concentration", "metric": "매출", "dim": "거래처", "title": "거래처 집중도"},
        {"block": "breakdown", "metric": "매출", "dim": "채널", "title": "채널별 구성"},
        {"block": "ranking", "metric": "매출", "dim": "제품", "limit": 15, "title": "제품 상위"},
        {"block": "total", "metric": "매출", "title": "기간 총량"},
    ],
    "timing": [
        {"block": "seasonality", "metric": "매출", "dim": "월", "title": "월별 계절성"},
        {"block": "promotion", "metric": "매출", "title": "프로모션 일정과 대조"},
        {"block": "trend", "metric": "매출", "dim": "월", "title": "월별 추세"},
        {"block": "total", "metric": "매출", "title": "기간 총량"},
    ],
    "ranking": [
        {"block": "ranking", "metric": "매출", "dim": "제품", "limit": 20, "title": "제품 상위"},
        {"block": "breakdown", "metric": "매출", "dim": "국가", "title": "국가별 구성"},
        {"block": "compare", "metric": "매출", "dim": "제품", "title": "제품별 전년 대비"},
        {"block": "total", "metric": "매출", "title": "기간 총량"},
    ],
}

_FALLBACK_LEDE = {
    "change": "무엇이 변화를 만들었는지 축별로 갈라 본다.",
    "efficiency": "비율로 효율을 보고, 규모는 배경으로 둔다.",
    "compare_target": "질문이 좁힌 대상을 나머지와 나란히 놓는다.",
    "concentration": "어디에 몰려 있고 그것이 어떤 위험인지 본다.",
    "timing": "달마다 다른 이유를 일정과 맞춰 본다.",
    "ranking": "무엇이 앞에 있고 그 구성이 어떤지 본다.",
    "size": "기간 전체 규모와 방향을 보고, 어디에서 왔는지 축별로 나눠 본다.",
}


def fallback_plan(question: str, ctx: Dict[str, Any]) -> Dict[str, Any]:
    """LLM 없이도 **유형에 맞는** 계획을 낸다."""
    det = detect(question)
    secs = _FALLBACK_SECTIONS.get(det["intent"])
    if not secs:
        secs = [
            {"block": "total", "metric": "매출", "title": "매출 총량"},
            {"block": "trend", "metric": "매출", "dim": "월", "title": "월별 추세"},
            {"block": "breakdown", "metric": "매출", "dim": "국가", "title": "국가별 구성"},
            {"block": "compare", "metric": "매출", "dim": "국가", "title": "국가별 전년 대비"},
            {"block": "breakdown", "metric": "매출", "dim": "채널", "title": "채널별 구성"},
            {"block": "ranking", "metric": "매출", "dim": "제품", "limit": 15,
             "title": "제품 상위"},
        ]
    return {
        "title": f"{ctx['focus_label']} 매출 보고서",
        "lede": _FALLBACK_LEDE.get(det["intent"], _FALLBACK_LEDE["size"]),
        "sections": [dict(s) for s in secs],
        "intent": det["intent"],
    }
