# -*- coding: utf-8 -*-
"""0행일 때 **어느 필터가 범인인지 실측한다.** 추측하게 두지 않는다.

⛔ 0건은 이 시스템에서 가장 조용한 실패다. 에러가 없고, LLM 이 그럴듯한 원인을
   지어내며, **그 설명이 사실처럼 읽힌다.** 실제로 겪은 것들:

     "에콰도르 Valkirias FOC 볼 수 있나"
       → "에콰도르는 유효 국가 목록에 존재하지 않습니다" (2026-08-14 제보)
         실제로는 2,448건·33.8억이 있었다. 힌트에 국가 12개만 나열돼 있었고
         LLM 이 그걸 전체 목록으로 읽었다
       → 국가 힌트를 전체 목록 대조로 고쳤더니 이번엔 **거래처명을 의심**했다
         ("Company_Name 이 등록 명칭과 다를 수 있다"). Valkirias 는 두 컬럼에
         모두 있었다 — 진짜 원인은 **그 기간에 샤셰 아닌 FOC 가 없던 것**이다

   컬럼마다 유효 값 목록을 붙이는 방식으로는 이 병을 못 고친다. 목록을 못 붙이는
   컬럼(거래처·제품)이 남고, 거기서 다시 추측한다. **필터를 하나씩 빼고 세어 보면
   범인이 그냥 나온다** — 한 번의 조회로 끝난다.

관련: `sql_agent.format_answer` 의 0행 경로에서만 부른다.
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional

import structlog

logger = structlog.get_logger(__name__)

# 절이 너무 적으면 뺄 게 없고, 너무 많으면 조회가 무거워진다
_MIN_CONDS, _MAX_CONDS = 2, 6
_TAIL = re.compile(r"\b(GROUP\s+BY|ORDER\s+BY|HAVING|LIMIT|WINDOW|QUALIFY)\b", re.I)


def split_and(clause: str) -> List[str]:
    """WHERE 절을 **최상위 AND** 로 자른다 (괄호·따옴표 안의 AND 는 건드리지 않는다).

    >>> split_and("a = 1 AND (b = 2 OR c = 3) AND d = 'x AND y'")
    ["a = 1", "(b = 2 OR c = 3)", "d = 'x AND y'"]
    """
    out, buf, depth, quote = [], [], 0, None
    i = 0
    while i < len(clause):
        ch = clause[i]
        if quote:
            buf.append(ch)
            if ch == "\\" and i + 1 < len(clause):     # 이스케이프는 통째로
                buf.append(clause[i + 1])
                i += 2
                continue
            if ch == quote:
                quote = None
        elif ch in "'\"`":
            quote = ch
            buf.append(ch)
        elif ch in "([":
            depth += 1
            buf.append(ch)
        elif ch in ")]":
            depth -= 1
            buf.append(ch)
        elif depth == 0 and clause[i:i + 5].upper() == " AND " :
            out.append("".join(buf).strip())
            buf = []
            i += 5
            continue
        else:
            buf.append(ch)
        i += 1
    tail = "".join(buf).strip()
    if tail:
        out.append(tail)
    return [c for c in out if c]


def _parse(sql: str) -> Optional[Dict]:
    """FROM 대상과 최상위 WHERE 조건들을 뽑는다. 확신이 없으면 None (진단을 건너뛴다)."""
    if not sql or sql.upper().count("SELECT") != 1:
        return None                                    # 서브쿼리·UNION 은 다루지 않는다
    m = re.search(r"\bFROM\s+(`[^`]+`)", sql, re.I)
    w = re.search(r"\bWHERE\b", sql, re.I)
    if not m or not w:
        return None
    rest = sql[w.end():]
    cut = _TAIL.search(rest)
    where = rest[:cut.start()] if cut else rest
    conds = split_and(where.strip().rstrip(";"))
    if not (_MIN_CONDS <= len(conds) <= _MAX_CONDS):
        return None
    return {"table": m.group(1), "conds": conds}


def diagnose(sql: str, bq) -> str:
    """필터를 하나씩 빼며 세어 **0행의 원인을 실측**한다. 실패하면 빈 문자열.

    한 번의 조회로 끝낸다 — 조건마다 `COUNTIF(나머지 전부)` 를 만든다.
    ⚠️ 이 함수는 **0행일 때만** 불린다. 성공 경로를 늦추지 않는다.
    """
    parsed = _parse(sql)
    if not parsed:
        return ""
    conds = parsed["conds"]
    cols = []
    for i, c in enumerate(conds):
        others = [o for j, o in enumerate(conds) if j != i]
        cols.append(f"COUNTIF({' AND '.join(f'({o})' for o in others)}) AS d{i}")
        # ⚠️ **"빼면 행이 생긴다" 를 "값이 틀렸다" 로 읽으면 안 된다.** 조합이 비었을 뿐
        #    값 자체는 멀쩡할 수 있다 — 실제로 `Company_Name='Valkirias'` 가 그랬다
        #    (206행 존재. LLM 은 "등록 명칭이 다를 것" 이라고 답했다). 그래서 **그 조건
        #    하나만으로도** 세어 값의 실재 여부를 따로 판정한다.
        cols.append(f"COUNTIF({c}) AS a{i}")
    probe = f"SELECT {', '.join(cols)} FROM {parsed['table']}"
    try:
        row = (bq.execute_query(probe) or [{}])[0]
    except Exception as e:
        # 파티션 필터 필수 등으로 못 돌 수 있다. 진단이 없을 뿐 답변은 계속된다
        logger.warning("zero_row_probe_failed", error=str(e)[:200])
        return ""

    stat = [(c, int(row.get(f"d{i}") or 0), int(row.get(f"a{i}") or 0))
            for i, c in enumerate(conds)]
    logger.warning("zero_row_diagnosed", sql=(sql or "")[:200],
                   result=[(c[:60], d, a) for c, d, a in stat])

    # 값이 실제로 없는 조건 = 진짜 잘못된 리터럴. 이것만 "값이 틀렸다" 고 말해도 된다
    missing = [(c, d) for c, d, a in stat if a == 0]
    present = [c for c, d, a in stat if a > 0]
    head = "🔎 **실측 진단** — 필터를 하나씩 빼고 실제로 세어 본 결과다. 여기 적힌 사실만 말하고, 다른 원인을 추측하지 마라:\n"
    lines = []
    if present:
        lines.append(
            "  - " + ", ".join(f"`{c}`" for c in present[:6])
            + " 는 **각각 단독으로는 데이터가 있다.** 이 값들이 잘못됐다거나 존재하지 "
              "않는다고 말하지 마라 (대소문자·표기·오타를 의심하지도 마라)")
    for c, d in missing:
        lines.append(f"  - `{c}` 는 **이 조건만으로도 0행이다** → 값이 실제로 존재하지 않는다")
    if not missing:
        lines.append(
            "  - 즉 어느 값도 틀리지 않았다. **조건들을 함께 걸었을 때 해당하는 데이터가 "
              "없는 것**이 0행의 원인이다. 그렇게만 설명하라")
    narrow = sorted([(c, d) for c, d, a in stat if d > 0], key=lambda kv: -kv[1])
    if narrow and not missing:
        lines.append("  - 참고로 `" + narrow[0][0] + "` 를 빼면 "
                     + f"{narrow[0][1]:,}행이 나온다 (범위를 넓힐 때 제안할 만한 축)")
    return head + "\n".join(lines)
