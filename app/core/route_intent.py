# -*- coding: utf-8 -*-
"""라우터 1단계 판정 — **순수 함수만** 둔다.

⛔ `orchestrator.py` 는 이미 4,282줄이다. 판정 규칙을 거기 더 쌓지 않는다
   (`textmatch.py`·`query_keywords.py` 와 같은 방식).

설계: `docs/superpowers/specs/2026-09-07-router-source-gate-design.md`
"""

from __future__ import annotations

import re

# 발화 **첫머리**의 부정. 실사용 60일 1,393건에서 20건이 걸렸고 전부 진짜
# 실패였다 (그중 붐따가 눌린 것은 0건 — 붐따와 겹치지 않는 신호다).
# ⛔ `말고` 를 여기 넣지 마라. 문장 중간의 `말고` 26건은 전부 정상적인 좁혀
#    묻기였다 ("상위 10개 말고 전체로 줘"). 걸면 멀쩡한 대화가 끊긴다.
_HEAD_REJECT = re.compile(
    r"^\s*(아니요|아니오|아뇨|아니|그게\s*아니|이게\s*아니|"
    r"틀렸|잘못|뭔\s*소리|엉뚱)", re.IGNORECASE)

# 부정을 걷어낸 뒤 남는 글자를 셀 때 지우는 것 — 공백·문장부호·감정 낱자
_FILLER = re.compile(r"[\s.!?~,;:·…\-()ㅠㅜㅋㅎ]+")

# 이보다 적게 남으면 「고쳐 말한 정보가 없다」로 본다
_BARE_MAX_CHARS = 4


def rejection_kind(text: str) -> str:
    """직전 답변을 부정하는 발화인가, 부정이라면 정보가 있는가.

    Returns:
        ``"none"``        — 부정이 아니다 (정상 라우팅)
        ``"informative"`` — 부정 + 고쳐 말한 내용이 있다 → 그 문장으로 다시 분류
        ``"bare"``        — 부정만 있다 → 되묻는다
    """
    body = " ".join((text or "").split())
    match = _HEAD_REJECT.match(body)
    if not match:
        return "none"
    rest = body[match.end():]
    return "bare" if len(_FILLER.sub("", rest)) <= _BARE_MAX_CHARS else "informative"


# ⛔ 이 문안은 **실측으로 고른 것**이다 (2026-09-07, 16문항 14/16).
#    같은 모델에게 "어느 소스냐"(6지선다)를 물으면 `노션` 낱말에 끌려
#    "Today 를 노션에 자동화할 수 없나?" 를 notion 으로 보낸다(붐따 #164).
#    예/아니오로 바꾸면 그 전제가 사라진다. **문구를 임의로 줄이지 마라.**
SOURCE_GATE_PROMPT = """당신은 사내 AI 어시스턴트의 1단계 판정기입니다.
질문 하나를 보고 **사내 자료(매출 데이터·사내 문서·제품 Q&A·개인 메일/일정)를
실제로 뒤져야 답할 수 있는지**만 판정하세요.

NEED = 사내 자료를 뒤져야 답할 수 있다 (수치·기록·사내 규정·내 메일 등)
SELF = 뒤질 필요가 없다. 다음 중 하나다:
       - 만들어 달라는 요청 (초안·구성안·아이디어·번역·코드·문장 다듬기)
       - 이 어시스턴트 자신의 기능·사용법·가능 여부를 묻는 질문
       - 용어의 뜻, 일반 상식, 잡담

주의 1. 질문에 사내 도구 이름(노션·잔디·틱톡샵)이 들어 있다는 이유만으로 NEED 로
        판정하지 마세요. **그 도구에 대해 묻는 것**과 **그 도구의 자료를 찾는 것**은
        다릅니다.
주의 2. 애매하면 NEED 를 고르세요. 뒤져 보고 없다고 말하는 편이, 뒤지지 않고
        지어내는 것보다 낫습니다.

NEED 또는 SELF 한 단어만 출력하세요."""


def parse_gate(raw) -> str:
    """게이트 응답을 읽는다. ⛔ 읽을 수 없으면 `NEED` — 안전한 쪽으로 실패한다.

    ⚠️ 한 단어만 요구했어도 모델이 문장을 붙일 수 있다. 둘 다 들어 있거나
       어느 쪽도 없으면 뒤지는 쪽을 고른다.
    """
    up = str(raw or "").upper()
    has_self, has_need = "SELF" in up, "NEED" in up
    return "SELF" if (has_self and not has_need) else "NEED"


# 사람이 읽는 경로 이름. ⛔ 내부 경로명("bigquery")을 그대로 보여주지 않는다
_ROUTE_LABEL = {
    "bigquery": "사내 데이터",
    "notion": "사내 문서",
    "cs": "제품 Q&A",
    "gws": "내 메일·드라이브·캘린더",
    "multi": "복합 분석",
    "direct": "일반 답변",
}


def clarify_message(prev_route) -> str:
    """부정만 하고 정보가 없을 때 되묻는 **고정 문구**.

    ⛔ LLM 을 부르지 않는다 (보증은 코드다).
    ⛔ 직전에 무엇을 했는지 **밝힌 뒤** 묻는다. 그냥 "무엇을 원하세요?" 라고만
       하면 사용자는 무엇을 고쳐 말해야 할지 모른다.
    """
    label = _ROUTE_LABEL.get(str(prev_route or ""), "")
    if not label:
        return ""
    return (f"방금은 **{label}** 에서 찾아 답했습니다. "
            f"어떤 걸 원하셨는지 한 줄만 더 알려주시겠어요?")


def source_free_footer() -> str:
    """사내 자료를 뒤지지 않고 답했을 때 붙이는 한 줄.

    ⛔ 되묻지 않는 대신 **돌아갈 문**을 준다. 사용자는 기다리지 않고 답을 받고,
       사내 데이터가 필요했다면 한 번 눌러서 간다.
    ⛔ 프롬프트로 시키지 않는다 — 정리 LLM 에게 맡기면 지워진다.
    """
    return "\n\n> 💡 사내 데이터로 확인해 드릴까요? 그렇게 말씀해 주시면 조회해 드립니다."
