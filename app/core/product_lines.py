# -*- coding: utf-8 -*-
"""질문이 어느 제품 라인을 지목했는지 결정적으로 판정한다.

⛔ 왜 필요한가 (붐따 #111, 2026-07-23):
       질문: "히알루 테카 라인 제품 정보 알려줘"
       답변: "히알루시카(Hyalucica) 라인 제품 정보 안내 …"

   **히알루테카(Hyalu_Teca)는 히알루시카와 다른 라인**이다. CS 검색이 '라인·제품·정보'
   같은 일반 낱말의 겹침만으로 히알루시카 Q&A 를 최상위로 올렸고, LLM 이 그대로 썼다.
   바꿔치기했다는 말은 답변 어디에도 없다 — 성분에서 '미상'을 '미포함'으로 쓴 것과
   같은 계열의 **조용한 오답**이다.

   CS 프롬프트에는 이미 "질문한 제품/브랜드와 다른 제품의 정보를 제공하지 마세요"가
   적혀 있었다. 그래도 났다 — 프롬프트는 확률을 높일 뿐이고 보증은 코드가 한다
   (국가·팀 리터럴 교정을 후처리로 둔 것과 같은 사상).

⛔ **어휘를 여기에 손으로 적지 않는다.** 단일 소스는 `prompts/sql_generator.txt` 의
   `### 제품 라인` 표다. 라인이 늘면 그 표만 고치면 여기까지 따라온다. 손으로 적은
   사본은 반드시 낡고, 낡으면 **에러가 아니라 조용한 오답**이 된다.

⚠️ 짧은 영문 별칭(`TB`·`LIN`)은 쓰지 않는다 — 정규화한 문자열 안에서 `lin` 이
   `line` 에 걸린다. 긴 영문 이름은 LIKE 패턴에서 얻는다 (`'%LabinNature%'` → labinnature).
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Dict, Set

import structlog

logger = structlog.get_logger(__name__)

_PROMPT = Path(__file__).resolve().parent.parent.parent / "prompts" / "sql_generator.txt"
_SECTION = "### 제품 라인"

# 정규화에서 지우는 것들 — "히알루 테카"·"히알루-테카"·"Hyalu_Teca" 를 한 형태로 모은다
_STRIP = re.compile(r"[\s\-_/·.]+")

# 이 길이 미만의 영문 별칭은 버린다 (TB·LIN 이 다른 낱말 안에 걸린다)
_MIN_ASCII_ALIAS = 6


def normalize(text: str) -> str:
    """공백·하이픈·밑줄을 지우고 소문자로 — 표기 차이를 없앤다."""
    return _STRIP.sub("", (text or "")).lower()


@lru_cache(maxsize=1)
def _aliases() -> Dict[str, str]:
    """정규화된 별칭 → 정식 라인명. 프롬프트의 '### 제품 라인' 표에서 읽는다."""
    out: Dict[str, str] = {}
    try:
        lines = _PROMPT.read_text(encoding="utf-8").splitlines()
    except OSError as e:
        # ⚠️ 조용히 비우면 이 방어선이 통째로 사라진다 — 흔적을 남긴다
        logger.warning("product_lines_prompt_unreadable", path=str(_PROMPT), error=str(e))
        return out

    inside = False
    for raw in lines:
        line = raw.strip()
        if line.startswith("###"):
            inside = line.startswith(_SECTION)
            continue
        if not inside or not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 2 or cells[0] in ("한국어",) or set(cells[0]) <= set("-: "):
            continue

        names = [n.strip() for n in cells[0].split(",") if n.strip()]
        if not names:
            continue
        canonical = normalize(names[0])
        if not canonical:
            continue
        for n in names:
            key = normalize(n)
            # 짧은 영문 약어는 다른 낱말 안에 걸린다 (LIN ⊂ line)
            if key.isascii() and len(key) < _MIN_ASCII_ALIAS:
                continue
            out[key] = canonical
        # LIKE 패턴의 영문 이름 — CS 시트나 SKU 가 영문일 때 이것이 붙잡는다
        for token in re.findall(r"%([A-Za-z0-9_]+)%", cells[1]):
            key = normalize(token)
            if len(key) >= _MIN_ASCII_ALIAS:
                out[key] = canonical
    return out


def known_lines() -> Set[str]:
    """정식 라인명 집합 (정규화된 형태)."""
    return set(_aliases().values())


def mentioned(text: str) -> Set[str]:
    """`text` 가 지목한 라인들.

    긴 이름이 이긴다 — '센텔라테카' 를 '센텔라' 로 읽으면 다시 다른 라인으로 답하게 된다.
    찾은 자리는 소비해서 접두사가 두 번 세지지 않게 한다.
    """
    hay = normalize(text)
    if not hay:
        return set()
    found: Set[str] = set()
    spans: list[tuple[int, int]] = []
    for alias in sorted(_aliases(), key=len, reverse=True):
        start = 0
        while True:
            i = hay.find(alias, start)
            if i < 0:
                break
            j = i + len(alias)
            if not any(s < j and i < e for s, e in spans):   # 이미 소비된 자리면 건너뛴다
                spans.append((i, j))
                found.add(_aliases()[alias])
            start = i + 1
    return found
