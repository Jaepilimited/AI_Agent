# -*- coding: utf-8 -*-
"""낱말 경계 매칭 — 한국어에서 짧은 낱말이 긴 낱말에 삼켜지는 것을 막는다.

한국어는 띄어쓰기가 낱말 경계를 보장하지 않아, 포함 검사(`"환율" in q`)가
**다른 뜻의 긴 낱말 안에서** 걸린다. 실제로 겪은 것들:

    "외부 요인도 같이 봐줘"      → `인도`   를 국가로 잡음  (보고서 필터)
    "인플루언서 시딩 가이드라인"   → `라인`   을 데이터로 잡음 (라우팅)
    "틱톡 광고 전환율 얼마야"     → `환율`   을 외부검색으로 잡음 (라우팅)

⛔ **모든 낱말에 경계를 강제하면 안 된다.** 한국어 합성어는 오른쪽으로 자연스럽게
   붙는다 — "월별매출"의 `매출`은 잡혀야 맞다. 그래서 `standalone()` 은
   **앞 글자만** 보고, 적용 대상도 뜻이 뒤집히는 낱말로 한정한다(`guarded`).

뒤는 보지 않는 이유: 조사가 붙는 것이 정상이다 ("베트남**과**", "매출**은**").
"""
from __future__ import annotations

from typing import Iterable, Optional, Set

_HANGUL_START, _HANGUL_END = "가", "힣"


def _is_hangul(ch: str) -> bool:
    return bool(ch) and _HANGUL_START <= ch <= _HANGUL_END


def standalone(text: str, word: str) -> bool:
    """`word` 가 더 긴 한글 낱말의 **일부가 아닌** 자리에 한 번이라도 나오는가.

    >>> standalone("외부 요인도 같이 봐줘", "인도")
    False
    >>> standalone("인도 매출 알려줘", "인도")
    True
    >>> standalone("베트남과 태국", "베트남")      # 뒤에 조사가 붙는 것은 정상
    True
    """
    if not text or not word:
        return False
    i = text.find(word)
    while i != -1:
        if not _is_hangul(text[i - 1] if i else ""):
            return True
        i = text.find(word, i + 1)
    return False


def contains_any(text: str, words: Iterable[str],
                 guarded: Optional[Set[str]] = None) -> bool:
    """`words` 중 하나라도 `text` 에 있는가. `guarded` 에 든 낱말만 경계를 본다."""
    guarded = guarded or set()
    for w in words:
        if w in guarded:
            if standalone(text, w):
                return True
        elif w in text:
            return True
    return False


def matches(text: str, words: Iterable[str],
            guarded: Optional[Set[str]] = None) -> list:
    """걸린 낱말 목록 (진단·테스트용)."""
    guarded = guarded or set()
    return [w for w in words
            if (standalone(text, w) if w in guarded else w in text)]
