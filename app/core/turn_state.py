# -*- coding: utf-8 -*-
"""대화 턴의 **조회 상태**를 구조로 들고 간다.

⛔ 지금까지 후속 질문이 참조하는 것은 **텍스트 뭉치**였다:
     - 직전 SQL 앵커 하나(600자에서 잘림 — 실측 40%가 초과)
     - 답변 원문(800~3000자에서 잘림, 표·차트는 제거됨)
   그래서 세 가지가 안 됐다:
     ① 두 턴 전 조회의 조건을 이어받기 (앵커는 직전 하나뿐)
     ② "첫 번째 질문"·"아까 일본 건" 을 특정 턴에 연결하기
     ③ 새 SQL 이 참조한 턴과 **말이 맞는지** 검증하기
   실제로 같은 대화 안에서 매출이 300억 달라진 적이 있다 (붐따 #116).

**설계**: 저장하지 않는다. `messages` 에서 매번 뽑는다.
  - 조회 SQL 은 이미 답변 안 `<details>` 에 들어 있다 — 그게 사실상의 로그다
  - 별도 테이블을 두면 동기화가 어긋나고, 지난 대화에는 소급되지 않는다
  - 파싱은 결정적이다 (LLM 을 쓰지 않는다)
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import structlog

from app.core.zero_row import split_and

logger = structlog.get_logger(__name__)

_SQL_IN_DETAILS = re.compile(r"```sql\s*(.*?)```", re.S)
_FROM = re.compile(r"\bFROM\s+`([^`]+)`", re.I)
_METRIC = re.compile(r"\b(SUM|COUNT|AVG|MIN|MAX)\s*\(\s*([^)]*?)\s*\)", re.I)
_WHERE_TAIL = re.compile(r"\b(GROUP\s+BY|ORDER\s+BY|HAVING|LIMIT|WINDOW|QUALIFY)\b", re.I)
# `Country = '일본'` / `Country IN ('일본','미국')` / `Team_NEW='JBT'`
_EQ = re.compile(r"\b(\w+)\s*=\s*'([^']*)'")
_IN = re.compile(r"\b(\w+)\s+IN\s*\(([^)]*)\)", re.I)
_DATE = re.compile(r"\b(?:Date|date|review_date)\b[^']*'(\d{4}-\d{2}-\d{2})")
# 기간·집계 컬럼은 '필터 값' 으로 보지 않는다 (참조 해석에 쓰면 잡음이 된다)
_NOT_A_VALUE = {"date", "review_date", "collected_date"}


@dataclass
class TurnState:
    """한 번의 조회가 무엇을 물었는지 — 구조로."""
    turn: int                                   # 1부터. 조회가 있었던 턴만 센다
    user_query: str = ""
    table: str = ""
    metrics: List[str] = field(default_factory=list)
    filters: Dict[str, List[str]] = field(default_factory=dict)
    period: Tuple[str, str] = ("", "")
    signature: str = ""

    def values(self) -> List[str]:
        """필터에 쓰인 값 전부 (참조 해석용)."""
        return [v for vs in self.filters.values() for v in vs]

    def summary(self) -> str:
        """컨텍스트에 실을 한 줄. 답변 원문 대신 이것을 넣는다."""
        parts = [f"턴{self.turn}"]
        if self.table:
            parts.append(self.table.split(".")[-1])
        if self.metrics:
            parts.append("/".join(self.metrics[:3]))
        for col, vs in list(self.filters.items())[:4]:
            parts.append(f"{col}={'|'.join(vs[:3])}")
        if self.period[0]:
            parts.append(f"기간 {self.period[0]}~{self.period[1] or ''}")
        return " · ".join(parts)


def _signature(sql: str) -> str:
    """공백·리터럴을 지운 SQL 지문 — 같은 조회인지 비교용."""
    s = re.sub(r"'[^']*'", "'?'", sql or "")
    s = re.sub(r"\s+", " ", s).strip().lower()
    return s[:400]


def parse_sql(sql: str) -> Dict[str, Any]:
    """SQL → {table, metrics, filters, period}. 결정적 파싱."""
    out: Dict[str, Any] = {"table": "", "metrics": [], "filters": {}, "period": ("", "")}
    if not sql:
        return out
    m = _FROM.search(sql)
    if m:
        out["table"] = m.group(1)
    seen = []
    for fn, arg in _METRIC.findall(sql):
        label = f"{fn.upper()}({arg.strip()})"
        if label not in seen:
            seen.append(label)
    out["metrics"] = seen[:5]

    w = re.search(r"\bWHERE\b", sql, re.I)
    if w:
        rest = sql[w.end():]
        cut = _WHERE_TAIL.search(rest)
        where = rest[:cut.start()] if cut else rest
        filters: Dict[str, List[str]] = {}
        for cond in split_and(where.strip().rstrip(";")):
            for col, val in _EQ.findall(cond):
                if col.lower() in _NOT_A_VALUE or not val:
                    continue
                filters.setdefault(col, [])
                if val not in filters[col]:
                    filters[col].append(val)
            for col, inner in _IN.findall(cond):
                if col.lower() in _NOT_A_VALUE:
                    continue
                vals = re.findall(r"'([^']*)'", inner)
                if vals:
                    filters.setdefault(col, [])
                    for v in vals:
                        if v and v not in filters[col]:
                            filters[col].append(v)
        out["filters"] = filters
    dates = _DATE.findall(sql)
    if dates:
        out["period"] = (min(dates), max(dates))
    return out


def extract_states(messages: List[Dict[str, str]]) -> List[TurnState]:
    """대화에서 조회 턴들의 상태를 순서대로 뽑는다."""
    states: List[TurnState] = []
    last_user = ""
    for msg in messages or []:
        role = msg.get("role", "")
        content = msg.get("content", "")
        if not isinstance(content, str):
            continue
        if role == "user":
            last_user = content.strip()
            continue
        if role not in ("assistant", "model"):
            continue
        sqls = _SQL_IN_DETAILS.findall(content)
        if not sqls:
            continue
        sql = sqls[-1].strip()
        p = parse_sql(sql)
        states.append(TurnState(
            turn=len(states) + 1, user_query=last_user[:200], table=p["table"],
            metrics=p["metrics"], filters=p["filters"], period=p["period"],
            signature=_signature(sql)))
    return states


# ── 2. 참조 해석 ────────────────────────────────────────────────────────────
_ORDINAL = {
    "첫": 1, "첫번": 1, "첫 번": 1, "처음": 1, "맨처음": 1, "맨 처음": 1,
    "두번": 2, "두 번": 2, "둘째": 2, "세번": 3, "세 번": 3, "셋째": 3,
    "네번": 4, "네 번": 4, "넷째": 4, "다섯번": 5, "다섯 번": 5,
}
_LAST_WORDS = ("마지막", "방금", "직전", "바로 위", "위에", "아까 그", "그거", "저거", "이거")
_BACK_REF = ("아까", "앞서", "이전", "전에", "위에서", "처음", "첫")


def resolve_reference(query: str, states: List[TurnState]) -> Optional[TurnState]:
    """"첫 번째 질문"·"아까 일본 건" 을 **특정 턴**에 결정적으로 연결한다.

    순서: 서수 → 값 매칭 → 마지막. 못 찾으면 None (호출부가 기본 동작을 한다).
    ⚠️ LLM 을 쓰지 않는다 — 참조 해석이 확률적이면 같은 질문이 매번 다르게 걸린다.
    """
    if not states or not query:
        return None
    q = query.strip()

    for word, n in sorted(_ORDINAL.items(), key=lambda kv: -len(kv[0])):
        if word in q and 1 <= n <= len(states):
            logger.info("turn_ref_ordinal", word=word, turn=n)
            return states[n - 1]

    # "아까 일본 건" — 뒤를 가리키는 말 + 이전 턴의 필터 값이 함께 있으면 그 턴이다
    if any(w in q for w in _BACK_REF):
        for st in reversed(states[:-1] or states):
            for v in st.values():
                if len(v) >= 2 and v in q:
                    logger.info("turn_ref_value", value=v, turn=st.turn)
                    return st

    if any(w in q for w in _LAST_WORDS):
        logger.info("turn_ref_last", turn=states[-1].turn)
        return states[-1]
    return None


# ── 3. 의미 일치 검증 ───────────────────────────────────────────────────────

def verify_alignment(new_sql: str, ref: Optional[TurnState],
                     query: str = "") -> Dict[str, Any]:
    """참조한 턴과 새 SQL 이 **말이 맞는가**. 실행 전에 부른다.

    ⛔ 참조 턴의 필터가 새 SQL 에서 **말없이 사라지면** 다른 것을 세게 된다.
       같은 대화에서 매출이 300억 달라진 사고가 그것이었다.
    ⚠️ 사용자가 바꾸라고 한 축은 어긋남이 아니다 — 질문에 그 값이나 컬럼이
       나오면 의도된 변경으로 본다.
    """
    res: Dict[str, Any] = {"ok": True, "dropped": [], "table_changed": False}
    if not ref or not new_sql:
        return res
    cur = parse_sql(new_sql)
    if ref.table and cur["table"] and ref.table != cur["table"]:
        res["table_changed"] = True
    q = (query or "").lower()
    for col, vals in ref.filters.items():
        if col in cur["filters"]:
            continue
        # 사용자가 그 축을 바꾸려 한 흔적이 있으면 어긋남이 아니다
        if col.lower() in q or any(v.lower() in q for v in vals):
            continue
        res["dropped"].append(f"{col}={'|'.join(vals[:3])}")
    res["ok"] = not res["dropped"] and not res["table_changed"]
    if not res["ok"]:
        logger.warning("turn_alignment_mismatch", ref_turn=ref.turn,
                       dropped=res["dropped"], table_changed=res["table_changed"],
                       query=(query or "")[:80])
    return res


# ── 4. 컨텍스트 압축 ────────────────────────────────────────────────────────

def compact_context(states: List[TurnState], keep_recent: int = 2) -> str:
    """오래된 턴은 **구조화 한 줄**로, 최근 턴만 자세히.

    ⚠️ 답변 원문·표·차트는 넣지 않는다 — 길이만 먹고 후속 해석에는 상태가 더 정확하다.
    """
    if not states:
        return ""
    lines = ["[이전 조회 상태 — 후속 질문은 이 축을 기준으로 해석]"]
    for st in states[:-keep_recent] if len(states) > keep_recent else []:
        lines.append("  " + st.summary())
    for st in states[-keep_recent:]:
        lines.append(f"  {st.summary()}  ← 질문: {st.user_query[:60]}")
    return "\n".join(lines)
