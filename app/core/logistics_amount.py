# -*- coding: utf-8 -*-
"""수출 물류 금액 = **유상 + 무상**. `amount` 만 쓰면 무상분이 통째로 빠진다.

붐따 #159 (2026-09-03, 전휘빈 제보):

    "주문 금액이 다르게 나와야 됩니다.
     (지금 나온 값은 유상 7555.72 인데 무상1,108.40 까지 합쳐진 8,664.12가
      출력되어야 함) 지금은 amount 컬럼의 값만 가져오는 것 같아요"

실측(`202606010105`)이 제보와 정확히 같다:

    amount 7,555.72 · free_amount 1,108.40 · total_amount **8,664.12**

⛔ **그런데 `total_amount` 만 쓰면 절반이 사라진다** (2026-09-03 프로덕션 실측,
   삭제 제외 2,491행):

    amount 있음                     2,231
    total_amount 있음               1,046
    ⛔ amount 는 있는데 total 없음   1,220   ← total 만 쓰면 이만큼 금액이 빈다
    ⛔ total 만 있고 amount 없음        35   ← amount+free 만 쓰면 이만큼 빠진다
    ⛔ 둘 다 있는데 값이 어긋남         107

   그래서 `IFNULL(total_amount, amount + IFNULL(free_amount, 0))` 이다.
   ⚠️ 둘 다 있으면 `total_amount` 를 믿는다 (2026-09-03 사용자 확정) — 시트에
      나중에 추가된 칸이고, 제보자가 그것을 답으로 지목했다.

**방어는 두 겹이고, 두 겹이 하는 일이 다르다**:

  1. `fix_sql()` — **집계 안의 `amount` 만** 고쳐 쓴다 (`SUM`·`AVG`·`MAX`·`MIN`).
     ⛔ 집계 밖의 `amount` 는 건드리지 않는다. 셀렉트 목록에서 바꾸려면 별칭을
        붙여야 하는데(`… AS amount`), 같은 토큰이 `GROUP BY`·`ORDER BY` 에도
        나올 수 있어 거기에 별칭을 붙이면 **SQL 이 깨진다**. 그리고 우리에겐
        SQL 파서가 없다 — `validate_sql()` 은 문법을 보지 않는다.
        고쳐 쓰는 것은 되돌릴 수 없으므로 **확실히 안전한 자리에서만** 한다
        (금액 배율 교정이 허용오차를 검증보다 엄격하게 둔 것과 같은 사상).
  2. `notice()` — 그 밖의 경우에는 **고치지 않고 말한다**. 유상만 나갔다는
     사실이 답변에 남으면, 사람이 8,664.12 를 기대하다 7,555.72 를 보고
     조용히 넘어가는 일은 없다.

생성 자체는 프롬프트(`prompts/sql_generator.txt` 테이블 16)가 맡는다 — 여기는
그것이 안 걸렸을 때의 그물이다.
"""
from __future__ import annotations

import re

import structlog

logger = structlog.get_logger(__name__)

TABLE = "export_logistics"

# 유상+무상. ⚠️ 한 곳에만 둔다 — 프롬프트·SQL 교정·회귀가 같은 문자열을 본다
TOTAL_EXPR = "IFNULL(total_amount, amount + IFNULL(free_amount, 0))"

# `SUM(amount)` · `sum( amount )` — 집계 안의 홑 `amount` 만.
# ⚠️ `\bamount\b` 는 `free_amount`·`total_amount` 에 매치되지 않는다
#    (`_` 가 단어 문자라 경계가 생기지 않는다). 그 성질에 기대므로 회귀가 지킨다.
_RE_AGG = re.compile(r"\b(SUM|AVG|MAX|MIN)\s*\(\s*amount\s*\)", re.IGNORECASE)

# 이 SQL 이 금액을 읽는가 (교정·공시 판정 공통)
_RE_BARE_AMOUNT = re.compile(r"\bamount\b", re.IGNORECASE)


def _touches_logistics(sql: str) -> bool:
    return TABLE in (sql or "").lower()


def fix_sql(sql: str) -> str:
    """집계 안의 `amount` 를 유상+무상으로 고친다. 그 밖은 그대로 둔다."""
    if not sql or not _touches_logistics(sql):
        return sql
    if "total_amount" in sql.lower():
        # 이미 총액을 다루고 있다 — 손대면 두 번 더하거나 뜻이 겹친다
        return sql
    fixed, n = _RE_AGG.subn(lambda m: f"{m.group(1)}({TOTAL_EXPR})", sql)
    if n:
        logger.info("logistics_amount_sql_fixed", replaced=n)
    return fixed


def notice(sql: str = "") -> str:
    """고쳐 쓰지 못한 채 유상 금액만 나가면 그 사실을 공시한다.

    ⛔ **표보다 먼저 말한다** — 뒤에 붙이면 후속 질문 제안·실행된 쿼리 뒤로
       밀려 정작 숫자를 읽는 사람 눈에 닿지 않는다 (같은 파일들의 다른 공시와
       같은 규칙).
    """
    low = (sql or "").lower()
    if not _touches_logistics(low):
        return ""
    if not _RE_BARE_AMOUNT.search(low):
        return ""                      # 금액을 안 물었다
    if "total_amount" in low:
        return ""                      # 총액을 쓰고 있다 — 공시할 것이 없다
    logger.info("logistics_amount_paid_only_notice")
    nl = chr(10)
    return (
        "> ⚠️ **아래 금액은 유상분만입니다 — 무상(FOC) 금액이 빠져 있습니다.**" + nl
        + "> 수출 금액은 보통 **유상 + 무상**으로 봅니다. 총액이 필요하시면 "
        + "「무상 포함 금액」·「총액」이라고 덧붙여 다시 물어봐 주세요."
        + nl + nl)
