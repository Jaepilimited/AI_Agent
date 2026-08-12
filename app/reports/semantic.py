# -*- coding: utf-8 -*-
"""의미론 계층 — 지표·축·필터를 검증된 어휘로 고정한다.

**보고서를 자유롭게 만들되 SQL 은 LLM 이 쓰지 않게 하는 장치.**
플래너(LLM)는 "어떤 지표를 어떤 축으로 볼까"만 고르고, 그 조합에서 SQL 은 여기서
결정적으로 만들어진다. 조합이 유한하고 전부 검증돼 있으므로 어떤 계획이 나와도
나오는 SQL 은 이미 아는 형태다.

CLAUDE.md 의 BigQuery 규칙이 여기 코드로 들어와 있다:
    - 매출 = SALES_ALL_Backup.Sales1_R
    - 수량 = **Product.Total_Qty** (테이블이 다르다. SALES_ALL 의 Total_Qty 는 세트를 1개로 센다)
    - 원가·FOC 는 FOC_or_Not 으로 분리 집계 (Production_Cost2 가 FOC 를 이미 포함)
    - 대륙은 Continent1
    - 팀은 Team_NEW, 표시할 때 한글로 되돌린다
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

SALES = "`skin1004-319714.Sales_Integration.SALES_ALL_Backup`"
PRODUCT = "`skin1004-319714.Sales_Integration.Product`"


@dataclass
class Metric:
    key: str
    label: str
    table: str
    expr: str
    unit: str = "억"
    scale: float = 1e8
    nd: int = 1
    desc: str = ""

    def select(self, alias: str = "value") -> str:
        if self.scale != 1:
            return f"ROUND({self.expr}/{self.scale:g}, {self.nd}) AS {alias}"
        return f"ROUND({self.expr}, {self.nd}) AS {alias}"


@dataclass
class Dimension:
    key: str
    label: str
    expr: str
    tables: List[str] = field(default_factory=lambda: [SALES, PRODUCT])
    relabel: Optional[str] = None      # 'team' 이면 코드→한글 치환
    exclude: List[str] = field(default_factory=list)
    desc: str = ""
    # 테이블마다 컬럼명이 다른 축이 있다. 예: 제품은 SALES 에서 `SET`, Product 에서 Product.
    # 하나로 뭉뚱그리면 "Unrecognized name" 으로 절 하나가 통째로 빠진다 (2026-08-12).
    expr_by_table: Dict[str, str] = field(default_factory=dict)

    def sql_expr(self, table: str) -> str:
        return self.expr_by_table.get(table, self.expr)


METRICS: Dict[str, Metric] = {
    "매출": Metric("매출", "매출", SALES, "SUM(Sales1_R)",
                 desc="원화 환산 실매출. FOC 행은 0으로 적재된다"),
    "수량": Metric("수량", "판매수량", PRODUCT, "SUM(Total_Qty)", unit="개", scale=1, nd=0,
                 desc="세트를 개별 SKU 로 분해한 수량. SALES_ALL 의 Total_Qty 를 쓰면 안 된다"),
    "유상원가": Metric("유상원가", "유상 제품원가", SALES,
                   "SUM(CASE WHEN FOC_or_Not='X' THEN Production_Cost2 ELSE 0 END)",
                   desc="무상 출고분을 뺀 원가"),
    "FOC원가": Metric("FOC원가", "FOC 원가", SALES,
                    "SUM(CASE WHEN FOC_or_Not='O' THEN Production_Cost2 ELSE 0 END)",
                    desc="무상 출고분 원가"),
    "할인": Metric("할인", "바우처·할인", SALES, "SUM(Discount_Coupon)",
                 desc="B2C 전용. B2B 는 전 구간 0원"),
    "주문수": Metric("주문수", "주문 건수", SALES, "COUNT(DISTINCT Order_Number)",
                  unit="건", scale=1, nd=0),
    "거래처수": Metric("거래처수", "거래처 수", SALES, "COUNT(DISTINCT ID)",
                   unit="곳", scale=1, nd=0),
}

DIMENSIONS: Dict[str, Dimension] = {
    "월": Dimension("월", "월", "FORMAT_DATE('%Y-%m', Date)"),
    "분기": Dimension("분기", "분기",
                    "CONCAT(CAST(EXTRACT(YEAR FROM Date) AS STRING), '-Q', "
                    "CAST(EXTRACT(QUARTER FROM Date) AS STRING))"),
    "국가": Dimension("국가", "국가", "Country", desc="한국어 국가명"),
    "대륙": Dimension("대륙", "대륙", "Continent1",
                    desc="광역 대륙은 반드시 Continent1 (Continent2 에는 '유럽'이 없다)"),
    "권역": Dimension("권역", "세부 권역", "Continent2", desc="동남아시아·서유럽 등"),
    "팀": Dimension("팀", "팀", "Team_NEW", relabel="team", exclude=["기타", "OP"]),
    "채널": Dimension("채널", "판매 채널", "Mall_Classification"),
    "브랜드": Dimension("브랜드", "브랜드", "Brand",
                     desc="CBT 는 스킨천사(SK)에 합산해야 한다"),
    "라인": Dimension("라인", "제품 라인", "Line",
                    exclude=["Others", "SET", "ZB", "B_Line", "C_Line", "E_Line", "Dual_Triple"]),
    "카테고리": Dimension("카테고리", "카테고리", "Category"),
    # ⚠️ SET 은 BigQuery 예약어다. 백틱을 벗기면 "Unexpected keyword SET" 로 터진다.
    #    그리고 Product 테이블에는 SET 이 없다 — 거기선 Product 컬럼이다.
    "제품": Dimension("제품", "제품", "`SET`",
                    expr_by_table={PRODUCT: "Product"}),
    "영업유형": Dimension("영업유형", "영업 유형", "Sales_Type"),
    "신규여부": Dimension("신규여부", "신규/기존 거래처", "New_Flag", tables=[SALES]),
}

# 필터로 쓸 수 있는 축 — 값은 리터럴 교정을 거친다
FILTERABLE = ["국가", "대륙", "권역", "팀", "채널", "브랜드", "라인", "카테고리", "영업유형"]


@dataclass
class Query:
    """블록이 만드는 조회 하나. SQL 문자열이 아니라 구조로 다룬다."""
    metric: str
    dim: Optional[str] = None
    dim2: Optional[str] = None
    filters: Dict[str, Any] = field(default_factory=dict)
    start: str = ""
    end: str = ""
    limit: int = 30
    order_desc: bool = True
    having_positive: bool = True


def _lit(v: Any) -> str:
    s = str(v).replace("'", "''")
    return f"'{s}'"


def _filter_sql(filters: Dict[str, Any], table: str) -> str:
    """필터 → WHERE 절. 등록된 축만 통과한다 — 임의 컬럼이 들어올 경로가 없다."""
    out = []
    for k, v in (filters or {}).items():
        if k not in DIMENSIONS:
            continue
        d = DIMENSIONS[k]
        if table not in d.tables:
            continue
        vals = v if isinstance(v, (list, tuple)) else [v]
        vals = [x for x in vals if x not in (None, "")]
        if not vals:
            continue
        joined = ", ".join(_lit(x) for x in vals)
        out.append(f"{d.sql_expr(table)} IN ({joined})")
    return "".join(f"\n  AND {c}" for c in out)


def build_sql(q: Query) -> str:
    """Query → BigQuery SQL. 여기가 유일한 SQL 생성 지점이다."""
    if q.metric not in METRICS:
        raise ValueError(f"등록되지 않은 지표: {q.metric}")
    m = METRICS[q.metric]
    table = m.table

    dims = [d for d in (q.dim, q.dim2) if d]
    for d in dims:
        if d not in DIMENSIONS:
            raise ValueError(f"등록되지 않은 축: {d}")
        if table not in DIMENSIONS[d].tables:
            raise ValueError(f"'{DIMENSIONS[d].label}' 축은 '{m.label}' 지표와 함께 쓸 수 없다")

    sel = [f"{DIMENSIONS[d].sql_expr(table)} AS {alias}"
           for d, alias in zip(dims, ("dim", "dim2"))]
    sel.append(m.select("value"))

    where = [f"Date BETWEEN {_lit(q.start)} AND {_lit(q.end)}"]
    where_extra = _filter_sql(q.filters, table)

    excl = []
    for d in dims:
        dd = DIMENSIONS[d]
        e = dd.sql_expr(table)
        if dd.exclude:
            joined = ", ".join(_lit(x) for x in dd.exclude)
            excl.append(f"{e} NOT IN ({joined})")
        excl.append(f"{e} IS NOT NULL")
        excl.append(f"{e} != ''")

    sql = f"SELECT\n  " + ",\n  ".join(sel) + f"\nFROM {table}\nWHERE " + " AND ".join(where)
    sql += where_extra
    for c in excl:
        sql += f"\n  AND {c}"

    if dims:
        sql += "\nGROUP BY " + ", ".join(str(i + 1) for i in range(len(dims)))
        if q.having_positive:
            sql += "\nHAVING value > 0"
        if q.dim in ("월", "분기") and not q.dim2:
            sql += "\nORDER BY dim"
        else:
            sql += f"\nORDER BY value {'DESC' if q.order_desc else 'ASC'}"
        sql += f"\nLIMIT {int(q.limit)}"
    else:
        sql += "\nLIMIT 1"
    return sql


def relabel_rows(rows: List[Dict[str, Any]], dim: Optional[str]) -> List[Dict[str, Any]]:
    """팀 코드를 한글 팀명으로 되돌린다 (표·차트 라벨이 한 번에 맞는다)."""
    if not dim or DIMENSIONS.get(dim, Dimension("", "", "")).relabel != "team":
        return rows
    try:
        from app.agents.sql_agent import TEAM_CODE2KR
    except Exception:
        return rows
    for r in rows:
        v = r.get("dim")
        if v in TEAM_CODE2KR:
            r["dim"] = f"{TEAM_CODE2KR[v]}({v})"
    return rows


def vocabulary() -> Dict[str, Any]:
    """플래너에게 줄 어휘표. 여기 없는 말은 계획에 쓸 수 없다."""
    return {
        "metrics": {k: {"label": v.label, "unit": v.unit, "desc": v.desc}
                    for k, v in METRICS.items()},
        "dimensions": {k: {"label": v.label, "desc": v.desc}
                       for k, v in DIMENSIONS.items()},
        "filterable": FILTERABLE,
    }
