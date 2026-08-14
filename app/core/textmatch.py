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


# 명사 뒤에 붙는 조사·어미. 긴 것부터 봐야 "에서"가 "서"로 잘리지 않는다
_PARTICLES = ("에서는", "에서도", "에서의", "에서", "으로는", "으로도", "으로", "로는",
              "에게서", "에게", "한테", "까지", "부터", "보다", "처럼", "만큼", "마다",
              "이라는", "라는", "이라고", "라고", "이란", "란",
              "의", "이", "가", "은", "는", "을", "를", "도", "만", "와", "과", "랑",
              "에", "로", "야", "아")


def strip_particle(token: str) -> str:
    """토큰 끝의 조사를 떼어 낸다 — 한국어는 교착어라 이걸 안 하면 불용어가 안 걸린다.

    ⛔ 실제 사고: 드라이브 검색이 `구글드라이브에서 내가 작성한 …` 을 통째로 검색어로
       넣어 **항상 0건**이었다. 불용어 목록에 `드라이브`·`에서`·`내` 가 있었는데
       조사가 붙어 한 덩어리라 하나도 안 걸렸다 (2026-08-14 사용자 제보).

    ⚠️ **두 글자 이하로 줄어들면 떼지 않는다** — "교안"의 '안', "자료"의 '료' 처럼
       멀쩡한 낱말이 잘려 나간다.

    >>> strip_particle("구글드라이브에서")
    '구글드라이브'
    >>> strip_particle("내가")
    '내'
    >>> strip_particle("교안")
    '교안'
    """
    t = (token or "").strip()
    for p in _PARTICLES:
        if t.endswith(p) and len(t) - len(p) >= 2:
            return t[: -len(p)]
    return t


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
