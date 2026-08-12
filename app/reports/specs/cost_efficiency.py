# -*- coding: utf-8 -*-
"""FOC·바우처 비용 효율화 보고서 스펙.

`매출분석/cost_efficiency/` 의 일회성 파이프라인을 재현 가능한 스펙으로 옮긴 것.
수치는 2026-08-12 실측으로 대조했다 (B2B FOC 113.7억 / B2C 할인 133.9억).

집계 계약은 CLAUDE.md 의 "원가·FOC·할인 집계 계약" 과 같다 —
`Production_Cost2` 가 FOC 원가를 이미 포함하므로 반드시 분리 집계한다.
"""
from __future__ import annotations

from typing import Any, Dict, List

from app.reports.spec import Fact, Gate, ReportSpec, Rows

T = "`skin1004-319714.Sales_Integration.SALES_ALL_Backup`"

# 집계 계약을 SQL 조각 하나로 고정한다. 각 fact 가 이걸 복사해 쓰면 정의가 갈리지 않는다.
COST_COLS = """
    ROUND(SUM(Sales1_R)/1e8, 1)                                                   AS sales,
    ROUND(SUM(CASE WHEN FOC_or_Not='X' THEN Production_Cost2 ELSE 0 END)/1e8, 1)  AS paid_cost,
    ROUND(SUM(CASE WHEN FOC_or_Not='O' THEN Production_Cost2 ELSE 0 END)/1e8, 1)  AS foc_cost,
    ROUND(SUM(Discount_Coupon)/1e8, 1)                                            AS discount
"""

BASE_WHERE = """
  WHERE Brand = '{brand}'
    AND Sales_Type IN ('B2B','B2C')
    AND Date BETWEEN '{start}' AND '{end}'
    AND Mall_Classification NOT IN ({excluded_channels})
"""

FACTS: List[Fact] = [
    Fact(
        id="contract",
        note="집계 계약이 이 기간에도 성립하는지 — 나머지 수치의 전제",
        expect="FOC_or_Not='O' 행은 매출 0, FOC=Production_Cost2. 'X' 행은 FOC=0",
        sql=f"""
        SELECT
          FOC_or_Not,
          COUNT(*)                                                     AS rows_n,
          COUNTIF(Sales1_R <> 0)                                       AS rows_sales_nonzero,
          COUNTIF(ABS(IFNULL(FOC,0)-IFNULL(Production_Cost2,0)) > 1)   AS rows_foc_ne_cost,
          ROUND(SUM(FOC)/1e8, 1)                                       AS foc_col
        FROM {T}
        WHERE Brand = '{{brand}}'
          AND Sales_Type IN ('B2B','B2C')
          AND Date BETWEEN '{{start}}' AND '{{end}}'
        GROUP BY 1 ORDER BY 1
        """,
    ),
    Fact(
        id="fee_integrity",
        note="수수료 컬럼을 비용에 넣어도 되는가 — 음수 적재 확인",
        expect="Service_Fee 합계가 음수이고 음수 행이 수십만 건 이상이면 제외가 맞다",
        sql=f"""
        SELECT
          ROUND(SUM(Service_Fee)/1e8, 1) AS service_fee,
          COUNTIF(Service_Fee < 0)       AS neg_rows,
          COUNT(*)                       AS rows_n
        FROM {T}
        WHERE Brand = '{{brand}}' AND Date BETWEEN '{{start}}' AND '{{end}}'
        """,
    ),
    Fact(
        id="zero_discount_channels",
        note="할인이 전 구간 0원인 채널 — 채널 할인율 비교의 분모를 망친다",
        expect="Tiktok 4개 채널이 12~19개월 전 구간 0원으로 잡힌다",
        sql=f"""
        SELECT
          Mall_Classification                        AS channel,
          COUNT(DISTINCT FORMAT_DATE('%Y-%m', Date)) AS months,
          ROUND(SUM(Sales1_R)/1e8, 1)                AS sales,
          COUNTIF(Discount_Coupon <> 0)              AS rows_with_discount
        FROM {T}
        WHERE Brand = '{{brand}}' AND Sales_Type = 'B2C'
          AND Date BETWEEN '{{start}}' AND '{{end}}'
        GROUP BY 1
        HAVING rows_with_discount = 0 AND months >= 6 AND sales > 1
        ORDER BY sales DESC
        """,
    ),
    Fact(
        id="summary",
        note="기간·유형별 핵심 집계 — 본문 요약표",
        expect="H1'26 B2B FOC원가 113.7억 / B2C 할인 121.5억+FOC행 12.4억 (틱톡 제외 전 기준)",
        sql=f"""
        SELECT
          CASE WHEN Date BETWEEN '{{focus_start}}' AND '{{focus_end}}' THEN 'focus'
               WHEN Date BETWEEN '{{compare_start}}' AND '{{compare_end}}' THEN 'compare'
               ELSE 'other' END AS period,
          Sales_Type,
          {COST_COLS}
        FROM {T}
        {BASE_WHERE}
        GROUP BY 1, 2
        HAVING period <> 'other'
        ORDER BY 1 DESC, 2
        """,
    ),
    Fact(
        id="monthly",
        note="월별 추이 — 방향이 악화인지 개선인지",
        expect="19개월(2025-01~2026-07) × 2유형",
        sql=f"""
        SELECT
          FORMAT_DATE('%Y-%m', Date) AS ym,
          Sales_Type,
          {COST_COLS}
        FROM {T}
        {BASE_WHERE}
        GROUP BY 1, 2 ORDER BY 1, 2
        """,
    ),
    Fact(
        id="by_team",
        note="팀별 편차 — 어디에 몰려 있는가",
        expect="B2B 는 영업1·2팀, B2C 는 마케팅 팀들에 집중",
        sql=f"""
        SELECT Team_NEW AS team, Sales_Type, {COST_COLS}
        FROM {T}
        {BASE_WHERE}
          AND Date BETWEEN '{{focus_start}}' AND '{{focus_end}}'
          AND Team_NEW NOT IN ('기타','OP')
        GROUP BY 1, 2
        HAVING sales > 0
        ORDER BY sales DESC
        """,
    ),
    Fact(
        id="by_channel",
        note="채널별 할인 프로파일 — 상한 시뮬의 입력",
        expect="채널별 할인율. 할인 미적재 채널은 게이트에서 이미 제외됨",
        sql=f"""
        SELECT Mall_Classification AS channel, {COST_COLS}
        FROM {T}
        {BASE_WHERE}
          AND Sales_Type = 'B2C'
          AND Date BETWEEN '{{focus_start}}' AND '{{focus_end}}'
        GROUP BY 1
        HAVING sales > 1
        ORDER BY discount DESC
        LIMIT 20
        """,
    ),
    Fact(
        id="b2b_accounts",
        note="B2B FOC 거래처 집중도 — 소수에 몰려 있으면 협상 한 건으로 해결된다",
        expect="거래처 400곳 안팎, 상위 5곳이 FOC 의 과반",
        # ⚠️ 억 단위로 반올림한 값으로 HAVING 을 걸면 5백만원 미만 거래처가 통째로 사라져
        #    거래처 수가 3분의 1로 줄어든다 (2026-08-12 원본 대조에서 발견). 원화 원값으로 거른다.
        sql=f"""
        SELECT
          ID AS account,
          SUM(Sales1_R)                                                  AS sales_won,
          SUM(CASE WHEN FOC_or_Not='O' THEN Production_Cost2 ELSE 0 END) AS foc_won
        FROM {T}
        {BASE_WHERE}
          AND Sales_Type = 'B2B'
          AND Date BETWEEN '{{focus_start}}' AND '{{focus_end}}'
        GROUP BY 1
        HAVING foc_won > 0
        ORDER BY foc_won DESC
        LIMIT 1000
        """,
    ),
]


# ── 품질 게이트 ────────────────────────────────────────────────────────────────

def _contract_holds(rows: Rows):
    bad = []
    for r in rows:
        if r["FOC_or_Not"] == "O":
            if r["rows_sales_nonzero"]:
                bad.append(f"FOC 행인데 매출≠0 {r['rows_sales_nonzero']}건")
            if r["rows_foc_ne_cost"]:
                bad.append(f"FOC≠Production_Cost2 {r['rows_foc_ne_cost']}건")
        elif (r["foc_col"] or 0) != 0:
            bad.append(f"유상 행인데 FOC {r['foc_col']}억")
    return (not bad), ("계약 성립" if not bad else " / ".join(bad))


def _fees_usable(rows: Rows):
    r = rows[0] if rows else {}
    neg = r.get("neg_rows") or 0
    total = r.get("service_fee") or 0
    if neg > 0:
        return False, f"Service_Fee 합계 {total}억, 음수 행 {neg:,}건"
    return True, "음수 적재 없음"


def _no_zero_discount_channels(rows: Rows):
    if not rows:
        return True, "할인 미적재 채널 없음"
    names = ", ".join(r["channel"] for r in rows[:6])
    lost = round(sum(r["sales"] or 0 for r in rows), 1)
    return False, f"{len(rows)}개 채널이 할인 전 구간 0원 ({names}) — 매출 {lost}억"


GATES = [
    Gate(
        id="contract",
        label="원가·FOC 집계 계약",
        fact="contract",
        verdict=_contract_holds,
        impact="집계 계약이 깨졌다. 이중계상 위험이 있어 보고서를 낼 수 없다.",
        blocking=True,   # 이게 깨지면 모든 비용 수치가 틀린다
    ),
    Gate(
        id="fees",
        label="판매수수료·물류비 사용 가능 여부",
        fact="fee_integrity",
        verdict=_fees_usable,
        impact="판매수수료·물류비는 원천 값이 음수로 적재돼 비용 집계에서 전면 제외했다. "
               "따라서 이 보고서의 비용은 수수료 차감 전이며, 진짜 수익성이 아니다.",
    ),
    Gate(
        id="zero_discount",
        label="할인 미적재 채널",
        fact="zero_discount_channels",
        verdict=_no_zero_discount_channels,
        impact="할인이 전 구간 0원으로 적재되는 채널을 집계에서 제외했다. "
               "포함하면 분모에 매출만 들어가 할인 비중이 실제보다 낮게 보인다. "
               "제외된 만큼 본 보고서의 매출·지출 총액은 실제보다 작다.",
    ),
]


# ── 파생 지표 (SQL 이 아니라 파이썬에서) ────────────────────────────────────────

def _eok(x) -> float:
    return round(float(x or 0), 1)


def _pct(num, den, nd=2):
    den = float(den or 0)
    return round(float(num or 0) / den * 100, nd) if den else 0.0


def derive(facts: Dict[str, Rows], params: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}

    # 기간별 손익 골격
    per: Dict[str, Dict[str, float]] = {}
    for r in facts.get("summary", []):
        p = per.setdefault(r["period"], {"sales": 0, "paid_cost": 0, "foc_cost": 0, "discount": 0})
        for k in ("sales", "paid_cost", "foc_cost", "discount"):
            p[k] += float(r[k] or 0)

    pnl = {}
    for key, p in per.items():
        direct = p["paid_cost"] + p["foc_cost"] + p["discount"]
        incentive = p["foc_cost"] + p["discount"]
        gross = p["sales"] - direct
        pnl[key] = {
            "sales": _eok(p["sales"]),
            "paid_cost": _eok(p["paid_cost"]),
            "foc_cost": _eok(p["foc_cost"]),
            "discount": _eok(p["discount"]),
            "direct_cost": _eok(direct),
            "gross_profit": _eok(gross),
            "gross_margin_pct": _pct(gross, p["sales"]),
            "incentive": _eok(incentive),
            "incentive_of_sales_pct": _pct(incentive, p["sales"]),
            "incentive_of_gross_pct": _pct(incentive, gross),
            "incentive_of_direct_pct": _pct(incentive, direct),
        }
    out["pnl"] = pnl

    # 전년 동기 대비 — 매출이 는 만큼 인센티브가 늘었는가 (한계율)
    cur, prv = pnl.get("focus"), pnl.get("compare")
    if cur and prv:
        d_sales = cur["sales"] - prv["sales"]
        d_inc = cur["incentive"] - prv["incentive"]
        out["yoy"] = {
            "sales_delta": _eok(d_sales),
            "incentive_delta": _eok(d_inc),
            "marginal_rate_pct": _pct(d_inc, d_sales),   # 늘어난 매출 1원당 인센티브
            "avg_rate_prev_pct": prv["incentive_of_sales_pct"],
            "avg_rate_cur_pct": cur["incentive_of_sales_pct"],
            # 평균율이 유지됐다면 썼을 금액을 넘어선 부분 = 관성으로 늘어난 몫
            "drift_cost": _eok(cur["incentive"] - cur["sales"] * prv["incentive_of_sales_pct"] / 100),
        }

    # B2B 거래처 집중도 — 비율은 원화 원값으로 계산하고, 표시할 때만 억으로 바꾼다
    accs = sorted(facts.get("b2b_accounts", []), key=lambda r: -float(r["foc_won"] or 0))
    total_foc = sum(float(r["foc_won"] or 0) for r in accs)

    def _top(n):
        return _pct(sum(float(r["foc_won"] or 0) for r in accs[:n]), total_foc)

    out["b2b_concentration"] = {
        "total_foc": _eok(total_foc / 1e8),
        "n_accounts": len(accs),
        "top5_pct": _top(5),
        "top10_pct": _top(10),
        "top20_pct": _top(20),
        "top": [
            {"account": r["account"],
             "sales": _eok(float(r["sales_won"] or 0) / 1e8),
             "foc_cost": _eok(float(r["foc_won"] or 0) / 1e8),
             "foc_rate_pct": _pct(r["foc_won"], r["sales_won"])}
            for r in accs[:10]
        ],
    }

    # 채널 할인 상한 시뮬레이션 — 정책 파라미터를 코드로 스윕한다
    chans = [r for r in facts.get("by_channel", []) if (r["sales"] or 0) > 0]
    sims = []
    for cap in params.get("discount_caps", [25, 20, 15]):
        save = 0.0
        n_over = 0
        for r in chans:
            rate = _pct(r["discount"], r["sales"])
            if rate > cap:
                save += float(r["discount"] or 0) - float(r["sales"] or 0) * cap / 100
                n_over += 1
        sims.append({"cap_pct": cap, "save": _eok(save), "n_channels_over": n_over})
    out["discount_cap_sim"] = sims

    # B2B FOC 상한 시뮬레이션
    b2b_sims = []
    for cap in params.get("foc_caps", [10, 8, 5]):
        save = 0.0
        n_over = 0
        for r in accs:
            sales_won = float(r["sales_won"] or 0)
            foc_won = float(r["foc_won"] or 0)
            if sales_won > 0 and _pct(foc_won, sales_won) > cap:
                save += foc_won - sales_won * cap / 100
                n_over += 1
        b2b_sims.append({"cap_pct": cap, "save": _eok(save / 1e8), "n_accounts_over": n_over})
    out["foc_cap_sim"] = b2b_sims

    return out


def build_spec(**overrides) -> ReportSpec:
    params: Dict[str, Any] = {
        "brand": "SK",
        "start": "2025-01-01",
        "end": "2026-07-31",
        "focus_start": "2026-01-01",
        "focus_end": "2026-06-30",
        "compare_start": "2025-01-01",
        "compare_end": "2025-06-30",
        # 본문에 숫자 리터럴을 남기지 않기 위해 기간 표기도 파라미터로 둔다 (렌더러 린터가 강제)
        "focus_label": "2026 상반기",
        "compare_label": "2025 상반기",
        "window_label": "2025년 1월 ~ 2026년 7월",
        # 게이트가 잡아낸 채널을 여기에 넣어 2회차부터 제외한다. 초기값은 비제외.
        "excluded_channels": "''",
        "discount_caps": [25, 20, 15],
        "foc_caps": [10, 8, 5],
    }
    params.update(overrides)
    return ReportSpec(
        id="cost_efficiency",
        title="FOC·바우처 비용 효율화",
        params=params,
        facts=FACTS,
        gates=GATES,
        derive=derive,
        template="cost_efficiency.html",
        # 데이터 값이 아닌 도메인 용어만 허용한다. 기간 표기(2026 상반기)까지 슬롯으로 넣어
        # 본문에 '옮겨 적은 수치'가 하나도 남지 않게 한다.
        allow_literals=["B2B", "B2C"],
    )
