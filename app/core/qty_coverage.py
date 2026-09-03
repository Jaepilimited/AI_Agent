# -*- coding: utf-8 -*-
"""판매수량을 셀 수 없는 브랜드 — 0 을 답으로 내보내지 않는다.

⛔ **왜 코드인가** (붐따 #156·#157, 2026-09-02 이형섭 제보 *"실제 물류 데이터와
   차이나는거 같습니다"*):

    주문번호 조회에 셀라가 이렇게 답했다.

        "**총 판매수량**: 0개 (데이터상 수량 미집계)"
        | UM | EUMDM005 | … | 269,706 | 0 | **0** | EUR |

    표의 `0` 은 **팔린 수량이 0** 이라는 뜻으로 읽힌다. 실제로는 이 브랜드의
    수량이 **어디에도 적재돼 있지 않다.** 모르는 것을 0 으로 적은 것이다.

**실측 (2026-09-03, 프로덕션 BigQuery · 2026년)**:

    SALES_ALL_Backup   UM   94,250행 → Total_Qty 합계 0 (NULL 아님, 전부 0)
                       CBT 970,509행 → Total_Qty 합계 0
                       SK  11,089,910행 → 85,687,259
    Product            SK 348,636행 · CL 28행 — **UM·CBT 행이 아예 없다**

    ⛔ 값이 `NULL` 이 아니라 **숫자 0** 이라 `SUM()` 도 `IFNULL` 도 "빈 결과
       정규화"도 걸리지 않는다. 그래서 조용히 "0개" 가 나간다 — 이 프로젝트가
       계속 겪어 온 **에러 없는 오답**의 전형이다.

이것은 새 규칙이 아니라 이미 아는 사실의 확장이다: 원가 미적재(UM 99.9% ·
CBT 99.6%)와 제품명(`SET`) 100% 공백이 같은 브랜드에서 이미 확인돼 있다
(CLAUDE.md). 수량도 같은 구멍이었을 뿐이다.

⛔ **값을 고치지 않는다.** ROAS 0 을 "최악" 으로 쓰지 않고 `산출 불가` 라고
   적는 것과 같다 — 조회 결과는 그대로 두고 **사실만 덧붙인다**.
⚠️ 그래서 이 공시는 **스스로 꺼진다**. 원본에 수량이 채워지면 UM 행이 더는
   0 이 아니고, 애초에 이 브랜드가 안 걸린 답변에는 처음부터 붙지 않는다.
   (매일 뜨는 경고는 곧 아무도 안 읽는다.)
"""
from __future__ import annotations

import re
from typing import Iterable, Optional, Sequence

import structlog

logger = structlog.get_logger(__name__)

# 데이터 코드 → 사람이 읽는 이름.
# ⚠️ `CBT` 는 브랜드가 아니라 팀 값이 `Brand` 칸에 잘못 들어간 것이고 실제로는
#    스킨천사 매출이다 (CLAUDE.md). 그래도 **수량이 없다는 사실은 같으므로**
#    여기서는 함께 다룬다 — 다만 "브랜드" 라고 단정하지 않는 이름을 쓴다.
UNCOUNTABLE_BRANDS = {
    "UM": "우마(UM)",
    "CBT": "CBT 표기분",
}

# 매출 쪽 수량 컬럼.
# ⛔ 물류의 `quantity_ea` 를 여기에 넣지 마라 — 그건 **발주·출고 수량**이고
#    판매 수량이 아니다 (2026-09-03 사용자 확정: *"물류는 발주쪽이고 매출은
#    실제 판매쪽임"*). 공시가 물류 답변에 붙으면 사실이 아닌 말을 하게 된다.
#    ⚠️ `\bquantity\b` 는 `quantity_ea` 에 매치되지 않는다(`_` 가 단어 문자다) —
#       그 성질에 기대고 있으므로 회귀가 양방향으로 지킨다.
_RE_QTY_COL = re.compile(
    r"\b(Total_Qty|FOC_Qty|Event_Qty|Qty_NameBase|Event_Qty_NameBase|Quantity)\b",
    re.IGNORECASE)

# 결과 행의 컬럼 이름이 수량으로 보이는가 (LLM 이 별칭을 붙인 경우).
_RE_QTY_KEY = re.compile(r"(^|_)(qty|quantity)($|_)|수량", re.IGNORECASE)

# `Brand` 를 이 값들로 좁힌 SQL. 컬럼이 결과에 안 실려도 대상을 알 수 있다.
_RE_BRAND_LITERAL = re.compile(r"Brand\s*(?:=|IN)\s*\(?\s*'([^']+)'", re.IGNORECASE)

_MAX_SCAN_ROWS = 5000     # 아주 큰 결과에서 공시 하나 때문에 전부 훑지 않는다


def _has_qty(sql: str, results: Optional[Sequence[dict]]) -> bool:
    """이 답변이 수량을 말하고 있는가."""
    if sql and _RE_QTY_COL.search(sql):
        return True
    for row in (results or [])[:1]:
        if any(_RE_QTY_KEY.search(str(k)) for k in row.keys()):
            return True
    return False


def _brand_values(results: Optional[Sequence[dict]]) -> set:
    """결과 행에 실린 Brand 값들. 컬럼이 없으면 빈 집합."""
    rows = list(results or [])[:_MAX_SCAN_ROWS]
    if not rows:
        return set()
    keys = [k for k in rows[0].keys() if str(k).strip().casefold() == "brand"]
    if not keys:
        return set()
    key = keys[0]
    return {str(r.get(key) or "").strip().upper() for r in rows}


def uncountable_in(sql: str = "", results: Optional[Sequence[dict]] = None) -> tuple:
    """이 답변에 수량을 셀 수 없는 브랜드가 걸려 있는가. 걸린 코드들을 돌려준다.

    ⛔ 수량을 말하지 않는 답변에는 절대 붙지 않는다 — UM 매출만 물었는데
       "수량을 셀 수 없습니다" 가 나가면 그건 소음이다.
    """
    if not _has_qty(sql, results):
        return ()

    hit = {b for b in _brand_values(results) if b in UNCOUNTABLE_BRANDS}

    # 결과에 Brand 컬럼이 없을 수도 있다 (집계 쿼리). SQL 이 그 브랜드로
    # 좁혔다면 대상이 확실하므로 그것도 본다.
    if not hit:
        literals = {m.upper() for m in _RE_BRAND_LITERAL.findall(sql or "")}
        if literals and literals <= set(UNCOUNTABLE_BRANDS):
            hit = literals

    return tuple(sorted(hit))


def _join_names(codes: Iterable[str]) -> str:
    return " · ".join(UNCOUNTABLE_BRANDS[c] for c in codes)


def notice(sql: str = "", results: Optional[Sequence[dict]] = None) -> str:
    """답변에 붙일 공시. 해당 없으면 빈 문자열.

    ⛔ **표보다 먼저 말한다.** 맨 뒤에 붙이면 후속 질문 제안·실행된 쿼리 뒤로
       밀려 정작 숫자를 읽는 사람 눈에 닿지 않는다 (물류 수량 공시가 실측으로
       겪은 것과 같은 문제 · OP 재고 신선도 공시와 같은 규칙).
    """
    codes = uncountable_in(sql, results)
    if not codes:
        return ""
    logger.info("qty_coverage_notice", brands=list(codes))
    nl = chr(10)
    return (
        "> ⚠️ **" + _join_names(codes) + " 의 판매수량은 집계할 수 없습니다.**" + nl
        + "> 이 데이터는 수량이 적재돼 있지 않아 조회 결과에 `0` 으로 나옵니다 — "
        + "**팔리지 않았다는 뜻이 아니라 수량을 모른다는 뜻**입니다." + nl
        + "> 매출 금액은 정상입니다. 발주·출고 수량은 `@@물류` 로 물어보세요."
        + nl + nl)


def prompt_fact(sql: str = "", results: Optional[Sequence[dict]] = None) -> str:
    """서술 단계에 못 박는 사실. 프롬프트는 확률이라 `notice()` 가 보증한다.

    ⚠️ 그래도 넣는 이유: 이게 없으면 LLM 이 요약에 "총 판매수량 0개" 라고 써
       버리고, 바로 위 공시와 **정면으로 어긋난 답변**이 나간다. 공시는 거짓말을
       막지만, 거짓말과 나란히 놓인 공시는 읽는 사람을 혼란스럽게 한다.
    """
    codes = uncountable_in(sql, results)
    if not codes:
        return ""
    nl = chr(10)
    return (
        nl + "⚠️ **수량 집계 불가 (확정된 사실 — 추측 금지)**: "
        + _join_names(codes) + " 은 수량이 적재되지 않아 결과의 수량이 0 으로 "
        "나옵니다. **'0개'·'판매수량 없음'·'미판매' 라고 쓰지 마세요.** "
        "'집계 불가' 라고 적고, 수량 합계·평균·비중을 만들지 마세요. "
        "금액은 그대로 답하면 됩니다." + nl)
