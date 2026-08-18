# -*- coding: utf-8 -*-
"""BigQuery 스키마 변화 감지 — **앱이 데이터 변화를 모르던 구멍**을 막는다.

⛔ 2026-08-18 실측으로 드러났다. 데이터분석파트가 리뷰 테이블을 국내/해외/매장으로
   통합했는데 **앱은 한 달 넘게 몰랐다.** 결과:
     - "국내몰 리뷰 2026년" → 4,140건 (실제 42,427건 · 스마트스토어만 셌다)
     - "플래그십 스토어 리뷰" → 조회조차 못 했다 (`Store_Review` 를 몰랐다)
   에러는 하나도 안 났다. 숫자가 그럴듯하게 작게 나왔을 뿐이라, 이주훈 님이
   제보하지 않았으면 계속 틀린 채로 답했다.

**감지 방식**: 매일 `INFORMATION_SCHEMA` 스냅샷을 떠서 어제와 비교한다.
  - 새 테이블 / 사라진 테이블
  - 컬럼 추가 / 삭제 / 타입 변경
  - ⚠️ **앱이 쓰는 테이블(화이트리스트)의 변화는 따로 표시**한다 — 거기가 오답으로
    이어지는 곳이다. 나머지는 참고용이다

⚠️ 감시 대상은 **앱이 실제로 보는 데이터셋**으로 한정한다. 프로젝트 전체는
   3,500개가 넘어(백업·테스트·중간 산출물) 매일 알림이 소음이 된다.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Set, Tuple

import structlog

from app.db.mariadb import execute, fetch_all, fetch_one

logger = structlog.get_logger(__name__)

PROJECT = "skin1004-319714"
# 앱이 실제로 조회하는 데이터셋만 본다 (전체는 소음이다)
WATCHED_DATASETS = (
    "Sales_Integration",
    "Review_Data",
    "marketing_analysis",
    "Platform_Data",
    "promotion_calendar",
)

_DDL = """
CREATE TABLE IF NOT EXISTS bq_schema_snapshot (
    id INT AUTO_INCREMENT PRIMARY KEY,
    taken_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    payload MEDIUMTEXT NOT NULL,
    table_count INT NOT NULL DEFAULT 0,
    column_count INT NOT NULL DEFAULT 0,
    INDEX idx_taken_at (taken_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""


def ensure_schema_watch_table() -> None:
    try:
        execute(_DDL)
    except Exception as e:
        logger.warning("schema_watch_table_error", error=str(e)[:160])


def _current() -> Dict[str, Dict[str, str]]:
    """지금 스키마 → `{"dataset.table": {"col": "TYPE"}}`."""
    from app.core.bigquery import get_bigquery_client

    bq = get_bigquery_client()
    out: Dict[str, Dict[str, str]] = {}
    for ds in WATCHED_DATASETS:
        try:
            rows = bq.execute_query(
                f"SELECT table_name, column_name, data_type "
                f"FROM `{PROJECT}.{ds}`.INFORMATION_SCHEMA.COLUMNS") or []
        except Exception as e:
            # 데이터셋 하나가 막혀도 나머지는 계속 본다
            logger.warning("schema_watch_dataset_failed", dataset=ds, error=str(e)[:120])
            continue
        for r in rows:
            out.setdefault(f"{ds}.{r['table_name']}", {})[r["column_name"]] = r["data_type"]
    return out


def _allowed_short() -> Set[str]:
    """화이트리스트를 `dataset.table` 형태로 (프로젝트 접두사 제거)."""
    from app.config import get_settings
    out = set()
    for t in get_settings().allowed_tables:
        parts = t.split(".")
        if len(parts) >= 3:
            out.add(".".join(parts[-2:]))
    return out


def _load_last() -> Tuple[Dict[str, Dict[str, str]], str]:
    row = fetch_one(
        "SELECT payload, taken_at FROM bq_schema_snapshot ORDER BY id DESC LIMIT 1")
    if not row:
        return {}, ""
    try:
        return json.loads(row["payload"]), str(row["taken_at"])[:16]
    except Exception:
        return {}, ""


def _save(cur: Dict[str, Dict[str, str]]) -> None:
    execute(
        "INSERT INTO bq_schema_snapshot (payload, table_count, column_count) "
        "VALUES (%s, %s, %s)",
        (json.dumps(cur, ensure_ascii=False), len(cur),
         sum(len(v) for v in cur.values())))
    # 스냅샷은 30개만 보관한다 (payload 가 크다)
    try:
        execute("DELETE FROM bq_schema_snapshot WHERE id NOT IN "
                "(SELECT id FROM (SELECT id FROM bq_schema_snapshot "
                " ORDER BY id DESC LIMIT 30) t)")
    except Exception:
        pass


def diff(prev: Dict[str, Dict[str, str]],
         cur: Dict[str, Dict[str, str]]) -> Dict[str, List[str]]:
    """어제 → 오늘 변화. 화이트리스트 테이블은 `_watched` 로 따로 모은다."""
    allowed = _allowed_short()
    res: Dict[str, List[str]] = {
        "added_tables": [], "removed_tables": [],
        "added_columns": [], "removed_columns": [], "changed_types": [],
        "watched": [],
    }

    def note(bucket: str, text: str, table: str) -> None:
        res[bucket].append(text)
        if table in allowed:
            res["watched"].append(text)

    for t in sorted(set(cur) - set(prev)):
        note("added_tables", f"+ 새 테이블 {t} ({len(cur[t])}컬럼)", t)
    for t in sorted(set(prev) - set(cur)):
        note("removed_tables", f"- 사라진 테이블 {t}", t)
    for t in sorted(set(prev) & set(cur)):
        pc, cc = prev[t], cur[t]
        for c in sorted(set(cc) - set(pc)):
            note("added_columns", f"+ {t}.{c} ({cc[c]})", t)
        for c in sorted(set(pc) - set(cc)):
            note("removed_columns", f"- {t}.{c}", t)
        for c in sorted(set(pc) & set(cc)):
            if pc[c] != cc[c]:
                note("changed_types", f"~ {t}.{c} {pc[c]} → {cc[c]}", t)
    return res


def run(save: bool = True) -> Dict[str, Any]:
    """스냅샷을 뜨고 어제와 비교한다. 첫 실행은 기준선만 저장한다."""
    ensure_schema_watch_table()
    cur = _current()
    if not cur:
        return {"ok": False, "detail": "스키마를 읽지 못했다"}
    prev, taken = _load_last()
    if save:
        _save(cur)
    if not prev:
        return {"ok": True, "baseline": True, "tables": len(cur),
                "columns": sum(len(v) for v in cur.values()),
                "detail": f"기준선 저장 ({len(cur)}개 테이블)"}
    d = diff(prev, cur)
    total = sum(len(v) for k, v in d.items() if k != "watched")
    if d["watched"]:
        # ⚠️ 앱이 쓰는 테이블의 변화만 실패로 올린다 — 나머지는 매일 뜨면 소음이다
        logger.warning("bq_schema_changed_watched", since=taken,
                       changes=d["watched"][:10], total=total)
    elif total:
        logger.info("bq_schema_changed", since=taken, total=total)
    return {"ok": True, "since": taken, "total": total, **d}
