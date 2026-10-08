# -*- coding: utf-8 -*-
"""CS 자료가 **어디까지 적재돼 있는지** 공시한다 — 붐따 #184 (2026-10-07).

제보는 링크 한 줄과 한 문장이었다:

    http://34.64.99.254:8061/dashboard?startDate=2026-09-01&endDate=2026-09-30
    대시보드내 수치와 다른 결과값이 나옵니다

**실측(2026-10-08)으로 원인을 갈랐다.** 날짜 상한을 잘못 건 것이 아니었다
(`received_at` 은 DATE 라 세 가지 표기 모두 같은 28을 준다). 적재가 멈춰 있었다:

    | | 9월 건수 | 가장 최근 접수일 |
    |---|---|---|
    | 대시보드 (`source=google_sheets`, 열 때마다 시트를 직접 읽는다) | **54** | 2026-09-30 |
    | 셀라 테이블 `cs_dashboard.domestic_cs_records` | **28** | **2026-09-16** |

    9/16 까지는 양쪽이 사실상 같다 (27 vs 28). 빠진 26건은 **전부 9/17 이후**이고
    10월은 한 행도 없다. 즉 **이 테이블의 ETL 이 3주 넘게 멈춰 있다.**

⛔ **고치는 것은 우리가 아니다** — 이 두 테이블은 *기존 ETL이 갱신한다*
   (`cs_metrics` 머리말). 우리가 할 수 있는 것은 **낡은 것을 낡았다고 말하는 것**이다.
   조용히 낡은 숫자를 자신 있게 답하는 것이 이 저장소의 기본 결함 유형이고,
   제보자가 본 것이 정확히 그것이다 — **에러도 경고도 없었다.**

⛔ **표보다 먼저 말한다.** `cs_metrics.notice()` 에 실어 **모든 답변 경로**에
   한 번에 붙인다 (사람은 표를 보지 각주를 안 본다 — OP 재고·물류 이상치와 같은 규칙).

⚠️ **스스로 꺼진다.** ETL 이 따라붙으면 조회 기간이 적재 끝일 안에 들어와 공시가
   사라진다. 영원히 뜨는 경고가 아니다 (매일 뜨는 경고는 곧 아무도 안 읽는다).

⚠️ 조회가 실패하면 **아무 말도 하지 않는다** — 조회가 죽었다고 멀쩡한 답변에
   경고를 붙이지 않는다 (안전한 쪽으로 실패).
"""
from __future__ import annotations

import datetime as _dt
import re
import time
from typing import Callable, Dict, Optional, Tuple

import structlog

from app.core.cs_metrics import DOMESTIC_TABLE, OVERSEAS_TABLE, tables_in_sql

logger = structlog.get_logger(__name__)

# ⛔ **기간 기준 컬럼은 프롬프트가 쓰라고 못 박은 것과 같아야 한다.** 다르면
#    조회가 고른 행과 공시가 센 행이 어긋나고, 그 어긋남은 티가 안 난다.
#    해외는 `DATE(refund_created_at, 'Asia/Seoul')` 이 기본 기준이다
#    (`received_at` 은 반품 연결이 없으면 NULL 이라 기준으로 쓰지 않는다).
_BASIS: Dict[str, Tuple[str, str]] = {
    DOMESTIC_TABLE: ("received_at", "국내 CS"),
    OVERSEAS_TABLE: ("DATE(refund_created_at, 'Asia/Seoul')", "해외 CS"),
}

_CACHE_TTL_SECONDS = 1800          # 질문 경로에서 BigQuery 를 매번 때리지 않는다
# ⚠️ 기간을 안 적은 질문(전체·CURRENT_DATE 기준)은 **이만큼 밀렸을 때만** 말한다.
#    하루이틀은 정상 적재 지연이고, 접수가 없는 주말도 있다. 넓게 둔다.
_STALE_DAYS = 4

_cache: Dict[str, Tuple[float, Optional[_dt.date]]] = {}

_re_date_literal = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
_re_current_date = re.compile(r"\bCURRENT_DATE\b|\bCURRENT_DATETIME\b|\bCURRENT_TIMESTAMP\b", re.I)


def _as_date(value) -> Optional[_dt.date]:
    if isinstance(value, _dt.datetime):
        return value.date()
    if isinstance(value, _dt.date):
        return value
    if isinstance(value, str):
        match = _re_date_literal.search(value)
        if match:
            return _dt.date(*(int(g) for g in match.groups()))
    return None


def coverage_end(table: str, *, force: bool = False,
                 fetcher: Optional[Callable[[str], object]] = None) -> Optional[_dt.date]:
    """그 테이블에 실제로 들어와 있는 **가장 최근 기준일**. 모르면 None."""
    basis = _BASIS.get(table)
    if not basis:
        return None
    now = time.time()
    cached = _cache.get(table)
    if not force and cached and (now - cached[0]) < _CACHE_TTL_SECONDS:
        return cached[1]

    def _query(expr: str):
        from app.core.bigquery import get_bigquery_client
        rows = get_bigquery_client().execute_query(
            f"SELECT MAX({expr}) AS coverage_end FROM `{table}`",
            timeout=60.0, max_rows=1) or []
        return rows[0].get("coverage_end") if rows else None

    try:
        raw = (fetcher or _query)(basis[0])
    except Exception as e:                        # noqa: BLE001 - 안전한 쪽으로 실패
        logger.warning("cs_coverage_probe_failed", table=table[-40:], error=str(e)[:160])
        return cached[1] if cached else None
    end = _as_date(raw)
    _cache[table] = (now, end)
    return end


def _queried_end(sql: str, today: _dt.date) -> Optional[_dt.date]:
    """질문이 **어디까지** 물었나. 날짜를 안 적었으면 None.

    ⚠️ 미래 상한(`'2026-10-31'`)으로 공시를 켜지 않는다 — 아직 오지 않은 기간이
       빠진 것은 적재 결손이 아니다. 그건 `_future_period_note` 가 맡는다.
    """
    if not sql:
        return None
    dates = []
    for match in _re_date_literal.finditer(sql):
        try:
            dates.append(_dt.date(*(int(g) for g in match.groups())))
        except ValueError:
            continue
    if _re_current_date.search(sql):
        dates.append(today)
    if not dates:
        return None
    return min(max(dates), today)


def notice_for_sql(sql: str, *, today: Optional[_dt.date] = None,
                   fetcher: Optional[Callable[[str], object]] = None) -> str:
    """적재가 질문한 기간에 못 미칠 때만 붙는 안내. 아니면 빈 문자열."""
    tables = tables_in_sql(sql or "")
    if not tables:
        return ""
    today = today or _dt.date.today()
    asked_end = _queried_end(sql, today)

    lines = []
    for table in sorted(tables):
        end = coverage_end(table, fetcher=fetcher)
        if end is None:
            continue
        label = _BASIS[table][1]
        if asked_end is not None:
            if asked_end <= end:
                continue
            missing = (asked_end - end).days
            lines.append(
                f"{label} 자료는 **{end:%Y-%m-%d} 까지만** 적재돼 있습니다 — "
                f"조회하신 {asked_end:%Y-%m-%d} 까지 중 뒤 {missing}일이 빠져 있어 "
                "대시보드 수치보다 적게 나옵니다 (대시보드는 원본 시트를 직접 읽습니다).")
        else:
            behind = (today - end).days
            if behind < _STALE_DAYS:
                continue
            lines.append(
                f"{label} 자료는 **{end:%Y-%m-%d} 까지만** 적재돼 있습니다 "
                f"({behind}일 전). 그 뒤 기록은 빠져 있어 대시보드 수치와 다릅니다.")
        logger.warning("cs_coverage_behind", table=table[-40:],
                       coverage_end=str(end), asked_end=str(asked_end or ""),
                       today=str(today))
    return "".join("> " + line + "\n" for line in lines)


def stale_tables(*, today: Optional[_dt.date] = None,
                 fetcher: Optional[Callable[[str], object]] = None) -> list:
    """자가 점검용 — 기준일이 `_STALE_DAYS` 넘게 밀린 테이블.

    ⛔ **ETL 이 우리 것이 아니라서 감시가 유일한 통로다.** 이 테이블이 멈춘 것을
       3주 동안 아무도 몰랐고, 사용자가 대시보드와 대조해 보고서야 드러났다.
    """
    today = today or _dt.date.today()
    out = []
    for table, (_, label) in _BASIS.items():
        end = coverage_end(table, force=True, fetcher=fetcher)
        if end is None:
            continue                      # 조회 실패를 '낡음' 으로 세지 않는다
        behind = (today - end).days
        if behind >= _STALE_DAYS:
            out.append({"table": table, "label": label,
                        "coverage_end": end, "days_behind": behind})
    return out
