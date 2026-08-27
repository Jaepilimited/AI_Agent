# -*- coding: utf-8 -*-
"""조직이 이미 검증한 질문↔SQL 을 자산으로 모아, 비슷한 질문에 **예시로** 준다.

⛔ **답을 재생하는 것이 아니다.** `sql_cache` 는 같은 질문에 같은 SQL 을 그대로 내주는데,
   그래서 **0건을 내는 SQL 도 그대로 재생한다**는 알려진 문제가 있다 (CLAUDE.md).
   여기는 다르다 — 뽑은 예시는 프롬프트에 *참고*로 들어가고, SQL 은 여전히 그 질문에
   맞게 새로 만들어진다. 검증·실행·후처리 방어선은 하나도 건너뛰지 않는다.

⛔ **틀린 것을 학습하면 더 자신 있게 틀린다.** 그래서 자산이 되는 조건을 좁게 잡는다:
     · 실제로 실행돼 **행이 나온** 것만 (0건 SQL 은 예시가 될 수 없다)
     · 두 번 이상 나온 것만 (한 번은 우연이다)
     · 👎 가 달린 대화에서 나온 것은 **막는다**
   막는 것은 되돌릴 수 있어야 하므로 지우지 않고 `blocked` 로 표시한다.

⛔ **예시 고르기에 LLM 을 쓰지 않는다.** 왜 이 예시가 프롬프트에 들어갔는지 로그로
   설명할 수 있어야 한다 — 낱말 겹침으로 고르고, 고른 근거를 함께 남긴다.

근거 (2026-08-27 실측): 같은 업무 질문을 여러 사람이 되풀이한다
(쇼피 인도네시아 매출 24회·6명, 아마존 미국 Top10 7회·6명). 그런데 조직이 이미
검증한 SQL 이 자산으로 남지 않아 매번 처음부터 만든다.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import structlog

from app.db.mariadb import execute, fetch_all

logger = structlog.get_logger(__name__)

#: 프롬프트에 넣을 예시 수. ⚠️ 늘리면 프롬프트가 길어지고 **엉뚱한 예시가 끼어들 확률**도
#:    같이 는다. 비슷한 것이 없으면 하나도 안 넣는 편이 낫다.
MAX_EXAMPLES = 2
#: 이만큼은 겹쳐야 "비슷한 질문" 이다. 낮추면 아무 예시나 붙는다.
MIN_OVERLAP = 0.34
#: 이만큼 나와야 자산으로 인정한다 — 한 번은 우연이다.
MIN_SEEN = 2

_DDL = """
CREATE TABLE IF NOT EXISTS sql_examples (
    id           INT AUTO_INCREMENT PRIMARY KEY,
    question_sig VARCHAR(190) NOT NULL,
    question     VARCHAR(500) NOT NULL,
    sql_text     TEXT NOT NULL,
    keywords     VARCHAR(500) NOT NULL DEFAULT '',
    rows_min     INT NOT NULL DEFAULT 0,
    n_seen       INT NOT NULL DEFAULT 1,
    blocked      TINYINT(1) NOT NULL DEFAULT 0,
    blocked_why  VARCHAR(200) NOT NULL DEFAULT '',
    first_seen   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_seen    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_sig (question_sig),
    INDEX idx_live (blocked, n_seen)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""


def ensure_tables() -> None:
    try:
        execute(_DDL)
    except Exception as e:
        logger.warning("sql_examples_ddl_failed", error=str(e)[:160])


def _keywords(question: str) -> List[str]:
    """검색어 추출은 **한 곳**에서만 한다 (`query_keywords`) — 사본을 만들지 않는다."""
    from app.core.query_keywords import extract

    return [w.lower() for w in extract(question or "", limit=12)]


# ── 쌓기 ─────────────────────────────────────────────────────────────────────

def record(question: str, sql: str, row_count: int) -> None:
    """실행이 **성공하고 행이 나왔을 때만** 후보로 쌓는다.

    ⚠️ 조용히 실패해야 한다 — 자산 축적이 답변 경로를 막으면 본말이 뒤집힌다.
    """
    question = str(question or "").strip()
    sql = str(sql or "").strip()
    if not question or not sql or row_count <= 0:
        return
    if len(question) > 400 or len(sql) > 6000:
        return                                  # 칩·프롬프트에 넣기엔 너무 크다

    from app.core.query_profile import signature

    sig = signature(question)[:190]
    if not sig:
        return
    try:
        ensure_tables()
        execute(
            "INSERT INTO sql_examples "
            "(question_sig, question, sql_text, keywords, rows_min, n_seen) "
            "VALUES (%s,%s,%s,%s,%s,1) "
            "ON DUPLICATE KEY UPDATE n_seen = n_seen + 1, last_seen = NOW(), "
            "sql_text = VALUES(sql_text), keywords = VALUES(keywords), "
            "rows_min = LEAST(rows_min, VALUES(rows_min))",
            (sig, question[:500], sql, " ".join(_keywords(question))[:500],
             int(row_count)),
        )
    except Exception as e:
        logger.warning("sql_example_record_failed", error=str(e)[:160])


def block(question: str, why: str) -> int:
    """👎 가 달린 질문의 예시를 막는다.

    ⚠️ 지우지 않는다 — 왜 막혔는지 남아 있어야 되돌릴 수 있고, 같은 질문이 다시
       쌓여 조용히 되살아나는 것도 막는다.
    """
    from app.core.query_profile import signature

    sig = signature(question)[:190]
    if not sig:
        return 0
    try:
        ensure_tables()
        return int(execute(
            "UPDATE sql_examples SET blocked = 1, blocked_why = %s "
            "WHERE question_sig = %s AND blocked = 0", (why[:200], sig)) or 0)
    except Exception as e:
        logger.warning("sql_example_block_failed", error=str(e)[:160])
        return 0


# ── 고르기 ───────────────────────────────────────────────────────────────────

def pick(question: str, allowed_tables: Optional[set] = None) -> List[Dict[str, Any]]:
    """이 질문과 **낱말이 충분히 겹치는** 예시를 고른다.

    ⛔ LLM 을 쓰지 않는다 — 왜 이 예시가 들어갔는지 설명할 수 있어야 한다.
    ⚠️ 허용되지 않은 테이블을 쓰는 예시는 뺀다. 손익(FI)처럼 사람마다 볼 수 있는
       범위가 다른 테이블이 예시로 새면 프롬프트에서 스키마를 지운 의미가 없다.
    """
    want = set(_keywords(question))
    if not want:
        return []
    try:
        ensure_tables()
        rows = fetch_all(
            "SELECT question, sql_text, keywords, n_seen FROM sql_examples "
            "WHERE blocked = 0 AND n_seen >= %s ORDER BY n_seen DESC LIMIT 400",
            (MIN_SEEN,),
        ) or []
    except Exception as e:
        logger.warning("sql_example_pick_failed", error=str(e)[:160])
        return []

    scored = []
    for row in rows:
        have = set(str(row.get("keywords") or "").split())
        if not have:
            continue
        overlap = len(want & have) / len(want | have)
        if overlap < MIN_OVERLAP:
            continue
        if allowed_tables is not None and not _tables_ok(row["sql_text"], allowed_tables):
            continue
        scored.append((overlap, int(row.get("n_seen") or 0), row))

    scored.sort(key=lambda x: (-x[0], -x[1]))
    picked = [
        {"question": r["question"], "sql": r["sql_text"],
         "overlap": round(o, 2), "n_seen": n}
        for o, n, r in scored[:MAX_EXAMPLES]
    ]
    if picked:
        logger.info("sql_examples_picked",
                    picked=[(p["question"][:40], p["overlap"]) for p in picked])
    return picked


def _tables_ok(sql: str, allowed_tables: set) -> bool:
    """예시가 건드리는 테이블이 전부 허용 목록 안인가 (대소문자 무관)."""
    lowered = str(sql or "").lower()
    import re

    used = set(re.findall(r"`([\w\-.]+\.[\w]+)`", lowered))
    if not used:
        return True
    allowed = {t.lower() for t in allowed_tables}
    return all(any(u.endswith(a.split(".")[-1]) or u == a for a in allowed) for u in used)


def render(examples: List[Dict[str, Any]]) -> str:
    """프롬프트에 넣을 블록. 없으면 빈 문자열 — 빈 제목만 남기지 않는다."""
    if not examples:
        return ""
    lines = [
        "",
        "## 사내에서 이미 검증된 비슷한 질문 (참고용)",
        "⚠️ 아래는 **참고**다. 지금 질문의 기간·필터가 다르면 그대로 쓰지 말고 고쳐 써라.",
        "   컬럼 선택과 조인 방식만 참고하라.",
    ]
    for ex in examples:
        lines.append("")
        lines.append(f"질문: {ex['question']}")
        lines.append("```sql")
        lines.append(ex["sql"].strip())
        lines.append("```")
    return "\n".join(lines)
