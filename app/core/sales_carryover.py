"""매출 이월 확인 — 두 날짜의 매출 백업 스냅샷을 거래처별로 견준다 (2026-09-23).

배경(사용자): 월말이면 거래처 매출이 이번 달에서 다음 달로 **이월**된다. 그걸 보려면
`SALES_ALL_Backup_20260912` 와 `SALES_ALL_Backup_20260930` 처럼 **과거에 백업한
매출 테이블**을 놓고 어떤 거래처가 빠졌는지 대조해야 한다. 조건은
`Brand IN ('SK','CBT') AND Sales_Type='B2B'`, 거래처는 `Company_Name`, 매출은 `Sales1_R`.

실측(2026-09-23, 스냅샷 8/31 ↔ 9/3, 대상월 2026-08):
- 스냅샷은 `Sales_Integration.SALES_ALL_Backup_YYYYMMDD` 로 **매일** 쌓인다 (362개,
  2025-09-03 ~ ). ⚠️ 빠진 날이 있다(9/13 등) — 날짜를 자유 입력받지 말고 **있는 것 중에서** 고른다
- 한 번 비교에 4초 · 411행. 대상월과 **그 다음 달**을 함께 읽어 "빠진 매출이 다음 달로
  갔는지" 를 같은 조회에서 본다 (Target: 8월 건수 29→20, 9월 207M→390M — 이월이다)
- ⛔ **줄어든 것이 전부 이월은 아니다.** 같은 조회에서 Notino·Razan 등 거래처 대부분이
  **약 4% 씩 일괄 감소**했다 — 건수는 그대로다. `Sales1_R` 이 원화 **환산값**이라 월말환율로
  다시 환산되면 전 거래처가 같은 비율로 움직인다. 그걸 이월로 읽으면 없는 이월을 쫓는다.
  그래서 **건수 변화**와 **공통 변동률(중앙값)** 을 함께 내고, 공통 변동이 크면 표보다 먼저 말한다

⛔ LLM 을 부르지 않는다. 두 표의 차이는 산술이고, 산술을 LLM 에 맡기면 확률이 된다
   (인증서류 찾기·성분·재고와 같은 사상)
⛔ 테이블 이름은 **실재하는 스냅샷 목록**으로만 만든다 — 문자열을 SQL 에 끼우는 자리라
   정규식 통과만으로는 부족하다. 목록에 없는 날짜는 거절한다
"""
from __future__ import annotations

import re
import statistics
import threading
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Callable, Dict, List, Optional

import structlog

logger = structlog.get_logger(__name__)

PROJECT_DATASET = "skin1004-319714.Sales_Integration"
SNAPSHOT_PREFIX = "SALES_ALL_Backup_"
_SNAPSHOT_RE = re.compile(r"^SALES_ALL_Backup_(\d{8})$")
_DATE_RE = re.compile(r"^\d{8}$")
_MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")

BRANDS = ("SK", "CBT")
SALES_TYPE = "B2B"

# 스냅샷 목록은 하루에 하나씩만 늘어난다 — 매 요청마다 __TABLES__ 를 읽을 이유가 없다
_SNAPSHOT_TTL_SECONDS = 30 * 60
# 공통 변동률이 이 이상이면 "환율 재환산 가능성" 을 표보다 먼저 말한다
COMMON_SHIFT_WARN_PCT = 1.0
# 화면 상한 — 거래처 수(실측 225)보다 넉넉하다. 넘치면 넘쳤다고 적는다
MAX_COMPANIES = 2000

_lock = threading.Lock()
_snapshot_cache: tuple[list[str], float] = ([], 0.0)


class CarryoverError(ValueError):
    """사용자에게 그대로 보여줄 수 있는 입력 오류."""


def _bq_client():
    from app.core.bigquery import BigQueryClient
    return BigQueryClient()


def list_snapshots(*, runner: Optional[Callable[[str], List[Dict[str, Any]]]] = None,
                   force: bool = False) -> list[str]:
    """실재하는 스냅샷 날짜(YYYYMMDD) 목록, 오름차순. 30분 캐시."""
    global _snapshot_cache
    with _lock:
        dates, stamp = _snapshot_cache
        if dates and not force and time.monotonic() - stamp < _SNAPSHOT_TTL_SECONDS:
            return list(dates)
    run = runner or _bq_client().execute_query
    rows = run(
        f"SELECT table_id FROM `{PROJECT_DATASET}.__TABLES__` "
        f"WHERE table_id LIKE '{SNAPSHOT_PREFIX}%' ORDER BY table_id"
    )
    found: list[str] = []
    for r in rows or []:
        m = _SNAPSHOT_RE.match(str(r.get("table_id") or ""))
        if m:
            found.append(m.group(1))
    found.sort()
    if found:
        with _lock:
            _snapshot_cache = (found, time.monotonic())
    return found


def default_month(snapshot_date: str) -> str:
    """대상월 기본값 = 앞 스냅샷 날짜의 달. 9/12 ↔ 9/30 을 고르면 9월을 본다."""
    return f"{snapshot_date[:4]}-{snapshot_date[4:6]}"


def _month_bounds(month: str) -> tuple[str, str, str]:
    """(대상월 1일, 다음달 1일, 다다음달 1일) — 다음 달까지 읽어 이월을 본다."""
    y, m = int(month[:4]), int(month[5:7])
    first = date(y, m, 1)
    nxt = date(y + (m // 12), (m % 12) + 1, 1)
    m2 = nxt.month
    nxt2 = date(nxt.year + (m2 // 12), (m2 % 12) + 1, 1)
    return first.isoformat(), nxt.isoformat(), nxt2.isoformat()


def _agg_sql(table: str, first: str, end: str) -> str:
    brands = ", ".join(f"'{b}'" for b in BRANDS)
    return (
        "SELECT Company_Name, FORMAT_DATETIME('%Y-%m', Date) AS ym, "
        "SUM(Sales1_R) AS s, COUNT(*) AS n "
        f"FROM `{PROJECT_DATASET}.{table}` "
        f"WHERE Brand IN ({brands}) AND Sales_Type = '{SALES_TYPE}' "
        f"AND Date >= DATETIME '{first}' AND Date < DATETIME '{end}' "
        "GROUP BY 1, 2"
    )


def build_sql(date_a: str, date_b: str, month: str) -> str:
    """두 스냅샷의 거래처×월 합계를 FULL OUTER JOIN 으로 나란히 놓는다.

    ⚠️ 호출 전에 `validate()` 를 거쳐야 한다 — 날짜가 실재 스냅샷 목록에 있어야 테이블 이름이 된다.
    """
    first, nxt, nxt2 = _month_bounds(month)
    a = _agg_sql(SNAPSHOT_PREFIX + date_a, first, nxt2)
    b = _agg_sql(SNAPSHOT_PREFIX + date_b, first, nxt2)
    return (
        f"WITH a AS ({a}), b AS ({b}) "
        "SELECT COALESCE(a.Company_Name, b.Company_Name) AS company, "
        "COALESCE(a.ym, b.ym) AS ym, a.s AS s_a, b.s AS s_b, a.n AS n_a, b.n AS n_b "
        "FROM a FULL OUTER JOIN b ON a.Company_Name = b.Company_Name AND a.ym = b.ym"
    )


def validate(date_a: str, date_b: str, month: Optional[str], snapshots: list[str]) -> tuple[str, str, str]:
    """입력을 거절하거나 (date_a, date_b, month) 로 정리한다. 앞 날짜가 더 이르게 정렬한다."""
    for d in (date_a, date_b):
        if not d or not _DATE_RE.match(d):
            raise CarryoverError("날짜는 YYYYMMDD 여덟 자리여야 합니다.")
        try:
            datetime.strptime(d, "%Y%m%d")
        except ValueError:
            raise CarryoverError(f"{d} 은 달력에 없는 날짜입니다.") from None
        if d not in snapshots:
            raise CarryoverError(f"{d[:4]}-{d[4:6]}-{d[6:]} 자 백업 테이블이 없습니다. 있는 날짜 중에서 골라 주세요.")
    if date_a == date_b:
        raise CarryoverError("같은 날짜 둘을 견줄 수는 없습니다. 서로 다른 두 날짜를 골라 주세요.")
    if date_a > date_b:
        date_a, date_b = date_b, date_a
    month = (month or "").strip() or default_month(date_a)
    if not _MONTH_RE.match(month):
        raise CarryoverError("대상월은 YYYY-MM 형식이어야 합니다.")
    return date_a, date_b, month


@dataclass
class CompanyRow:
    company: str
    s_a: float
    s_b: float
    n_a: int
    n_b: int
    next_a: float
    next_b: float
    status: str = ""
    diff: float = 0.0
    pct: Optional[float] = None
    next_diff: float = 0.0

    def finish(self) -> "CompanyRow":
        self.diff = self.s_b - self.s_a
        self.pct = (self.diff / self.s_a * 100.0) if self.s_a else None
        self.next_diff = self.next_b - self.next_a
        if self.s_a and not self.s_b:
            self.status = "사라짐"
        elif not self.s_a and self.s_b:
            self.status = "신규"
        elif abs(self.diff) < 1:
            self.status = "동일"
        elif self.diff < 0:
            self.status = "감소"
        else:
            self.status = "증가"
        return self

    def as_dict(self) -> Dict[str, Any]:
        return {
            "company": self.company, "status": self.status,
            "s_a": round(self.s_a), "s_b": round(self.s_b), "diff": round(self.diff),
            "pct": None if self.pct is None else round(self.pct, 1),
            "n_a": self.n_a, "n_b": self.n_b,
            "next_a": round(self.next_a), "next_b": round(self.next_b), "next_diff": round(self.next_diff),
        }


@dataclass
class Comparison:
    date_a: str
    date_b: str
    month: str
    next_month: str
    rows: List[CompanyRow]
    notices: List[str] = field(default_factory=list)
    truncated: int = 0

    @property
    def total_a(self) -> float:
        return sum(r.s_a for r in self.rows)

    @property
    def total_b(self) -> float:
        return sum(r.s_b for r in self.rows)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "date_a": self.date_a, "date_b": self.date_b, "month": self.month, "next_month": self.next_month,
            "filter": {"brand": list(BRANDS), "sales_type": SALES_TYPE},
            "summary": {
                "companies": len(self.rows),
                "total_a": round(self.total_a), "total_b": round(self.total_b),
                "diff": round(self.total_b - self.total_a),
                "gone": sum(1 for r in self.rows if r.status == "사라짐"),
                "decreased": sum(1 for r in self.rows if r.status == "감소"),
                "increased": sum(1 for r in self.rows if r.status == "증가"),
                "new": sum(1 for r in self.rows if r.status == "신규"),
                "truncated": self.truncated,
            },
            "notices": list(self.notices),
            "rows": [r.as_dict() for r in self.rows],
        }


def common_shift_pct(rows: List[CompanyRow]) -> Optional[float]:
    """양쪽에 다 있고 **건수가 같은** 거래처들의 변동률 중앙값. 건수가 같은데 금액이
    일제히 움직였으면 이월이 아니라 재환산이다 (실측: 8/31↔9/3 이 −4% 대로 일제히 움직였다).
    셋 미만이면 판단하지 않는다 (None)."""
    pcts = [r.pct for r in rows
            if r.s_a and r.s_b and r.n_a == r.n_b and r.pct is not None]
    if len(pcts) < 3:
        return None
    return statistics.median(pcts)


def assemble(date_a: str, date_b: str, month: str, raw_rows: List[Dict[str, Any]]) -> Comparison:
    """조회 결과(거래처×월 행) → 거래처 한 줄. 대상월이 본 값, 다음 달은 이월 신호로 붙인다."""
    _, nxt, _ = _month_bounds(month)
    next_month = nxt[:7]
    by_company: Dict[str, CompanyRow] = {}
    for r in raw_rows:
        name = r.get("company")
        if name is None:
            name = "(거래처 없음)"
        row = by_company.get(name)
        if row is None:
            row = by_company[name] = CompanyRow(name, 0.0, 0.0, 0, 0, 0.0, 0.0)
        ym = r.get("ym")
        s_a, s_b = float(r.get("s_a") or 0.0), float(r.get("s_b") or 0.0)
        if ym == month:
            row.s_a, row.s_b = s_a, s_b
            row.n_a, row.n_b = int(r.get("n_a") or 0), int(r.get("n_b") or 0)
        elif ym == next_month:
            row.next_a, row.next_b = s_a, s_b
    rows = [row.finish() for row in by_company.values()
            if row.s_a or row.s_b]   # 대상월에 아무것도 없는 거래처는 이 표의 대상이 아니다
    # 빠진 것부터: 사라짐 → 감소(큰 순) → 동일 → 증가 → 신규
    order = {"사라짐": 0, "감소": 1, "동일": 2, "증가": 3, "신규": 4}
    rows.sort(key=lambda r: (order[r.status], r.diff, -r.s_a))
    truncated = 0
    if len(rows) > MAX_COMPANIES:
        truncated = len(rows) - MAX_COMPANIES
        rows = rows[:MAX_COMPANIES]
    cmp = Comparison(date_a, date_b, month, next_month, rows, truncated=truncated)
    shift = common_shift_pct(rows)
    if shift is not None and abs(shift) >= COMMON_SHIFT_WARN_PCT:
        cmp.notices.append(
            f"건수가 그대로인 거래처들의 매출이 한꺼번에 {shift:+.1f}% 움직였습니다. "
            "매출(원화 환산값)이 월말환율로 다시 환산됐을 가능성이 큽니다 — "
            "금액만 줄고 건수가 같은 행은 이월이 아닐 수 있습니다. "
            "이월은 '건수'가 줄고 다음 달이 늘어난 행으로 보세요.")
    if not rows:
        cmp.notices.append(f"{month} 에 조건에 맞는 B2B 매출이 두 스냅샷 어디에도 없습니다.")
    if truncated:
        cmp.notices.append(f"거래처가 {MAX_COMPANIES:,}곳을 넘어 {truncated:,}곳을 표에서 뺐습니다.")
    return cmp


def compare(date_a: str, date_b: str, month: Optional[str] = None, *,
            runner: Optional[Callable[[str], List[Dict[str, Any]]]] = None,
            snapshots: Optional[list[str]] = None) -> Comparison:
    """입력 검증 → 조회 → 조립. `runner`/`snapshots` 는 테스트용 주입점이다."""
    snaps = snapshots if snapshots is not None else list_snapshots(runner=runner)
    date_a, date_b, month = validate(date_a, date_b, month, snaps)
    run = runner or _bq_client().execute_query
    t0 = time.monotonic()
    raw = run(build_sql(date_a, date_b, month))
    result = assemble(date_a, date_b, month, raw or [])
    logger.info("sales_carryover_compared", date_a=date_a, date_b=date_b, month=month,
                rows=len(result.rows), seconds=round(time.monotonic() - t0, 1))
    return result
