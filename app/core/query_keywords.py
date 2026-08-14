# -*- coding: utf-8 -*-
"""질문 → 검색 키워드. **한 곳에서만 뽑는다.**

⛔ 예전엔 검색하는 곳마다 각자 불용어 목록을 갖고 있었고, **어느 것도 조사를 떼지
   않았다.** 한국어는 교착어라 그러면 조용히 빗나간다:

    드라이브 : "구글드라이브에서 내가 작성한 신규 입사자 교안 자료 찾아줘"
               → 문장 전체가 검색어가 돼 **항상 0건** (2026-08-14 사용자 제보,
                 제미나이는 찾는 파일을 우리만 못 찾았다)
    위키     : "일본에서 매출이 왜 늘었는지" → `매출이`·`일본에서` 로 LIKE 매칭 →
               "매출" 이 든 문서를 못 찾는다

두 곳 다 **에러가 나지 않는다.** "검색 결과가 없습니다" 는 정말 없을 때와 검색어가
망가졌을 때가 똑같이 생겼다 — 그래서 오래 안 잡혔다.

사용하는 곳: `gws_agent`(드라이브) · `wiki_search`. 새 검색 경로도 여기를 쓴다.
"""
from __future__ import annotations

import re
from typing import Iterable, List, Optional, Set

import structlog

from app.core.textmatch import strip_particle

logger = structlog.get_logger(__name__)

# 한글·영문·숫자로 시작하고 하이픈/슬래시/점으로 이어지는 덩어리 (B2B, 2026-08, A/B 보존)
_TOKEN = re.compile(r"[가-힣A-Za-z0-9][가-힣A-Za-z0-9\-_/.]*")

# 어느 검색에서나 뜻을 좁히지 못하는 말 — 명령·의문·지시어와 너무 일반적인 명사
BASE_STOP: Set[str] = {
    # 명령·요청
    "찾아줘", "찾아", "찾기", "검색해줘", "검색", "보여줘", "보여", "알려줘", "알려",
    "해줘", "주세요", "부탁", "부탁해", "정리해줘", "설명해줘", "말해줘",
    # 의문·지시
    "뭐", "뭐야", "무엇", "무슨", "어디", "어느", "언제", "누가", "왜", "어떻게",
    "어떤", "이거", "그거", "저거", "이것", "그것", "관련", "관련된", "대한", "대해",
    # 너무 일반적인 명사
    "자료", "파일", "폴더", "문서", "내용", "정보", "것", "거", "좀", "때", "중",
    "있는", "없는", "있어", "있나", "있지", "된", "하는", "한", "들", "등",
    # 인칭·소유
    "내", "내가", "나의", "제", "제가", "저의", "우리", "우리의", "너", "당신",
}


def extract(question: str, extra_stop: Optional[Iterable[str]] = None,
            min_len: int = 2, limit: int = 8) -> List[str]:
    """검색에 쓸 키워드만 남긴다.

    ⚠️ **조사를 떼고 나서** 불용어를 본다. 순서를 바꾸면 `내가`·`매출이` 가 그대로
       남아 아무것도 안 걸린다 — 이 함수가 생긴 이유다.
    ⚠️ 원문 표기를 유지한다 (`B2B`·`GM WEST` 처럼 대문자가 뜻인 경우가 있다).

    >>> extract("구글드라이브에서 내가 작성한 신규 입사자 교안 자료 찾아줘",
    ...         extra_stop={"구글드라이브", "작성한"})
    ['신규', '입사자', '교안']
    """
    stop = set(BASE_STOP) | {s.lower() for s in (extra_stop or ())}
    out: List[str] = []
    seen: Set[str] = set()
    for raw in _TOKEN.findall(question or ""):
        tok = strip_particle(raw)
        low = tok.lower()
        if len(tok) < min_len or low in stop or low in seen:
            continue
        # 조사를 뗀 뒤에도 불용어면 버린다 ("매출이" → "매출" 은 남고, "것을" → "것" 은 간다)
        seen.add(low)
        out.append(tok)
        if len(out) >= limit:
            break
    return out


def log_empty(source: str, question: str, keywords: List[str], **extra) -> None:
    """0건일 때 **왜 0건인지** 남긴다.

    ⛔ "검색 결과가 없습니다" 는 정말 없을 때와 검색어가 망가졌을 때가 똑같이 생겼다.
       검색어를 함께 남겨야 나중에 구분할 수 있다 (프로덕션은 INFO 를 버리므로 WARNING).
    """
    logger.warning("search_empty", source=source, question=(question or "")[:120],
                   keywords=keywords, **extra)
