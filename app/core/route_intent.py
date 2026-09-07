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
