# -*- coding: utf-8 -*-
"""빠른 응답 요약 한 줄 — **라벨이 값의 종류를 거짓말하지 않는다.**

2026-09-09 붐따 스윕에서 실측으로 걸렸다. 물류 주문 금액을 물었더니 요약이

    #### 요약  총 주문 **8,664건**

으로 나갔다. 8,664.12 는 **USD 금액**(`total_order_amount`)이지 건수가 아니다.
`"order" in 컬럼명` 이면 무조건 「건」으로 세고 있었다.

⚠️ 표는 옆에 맞게 있어서 **오히려 더 믿긴다** — 붐따 #138(`제품명: 숫자`)·
   #162(물류비를 「한화 수출금액」이라 부름)와 같은 계열의 조용한 오답이다.
"""
import pytest

from app.agents.sql_agent import _fast_summary_line as summary


def test_an_amount_column_is_never_counted_as_rows():
    """⛔ #159 그 자체 — `total_order_amount` 는 금액이다."""
    line = summary([{"order_number": "202606010105", "currency": "USD",
                     "paid_amount": 7555.72, "free_amount": 1108.40,
                     "total_order_amount": 8664.12}])
    assert "건" not in line
    assert "8,664" not in line


def test_a_foreign_amount_is_not_called_revenue():
    """⚠️ 통화가 섞일 수 있고 매출도 아니다 — 말하지 않는 것이 맞다.

    「매출액 억원」을 붙이면 라벨이 또 거짓말을 한다 (통화 혼재 합계 금지와 같은 이유).
    """
    line = summary([{"paid_amount": 7555.72}, {"paid_amount": 100.0}])
    assert "매출" not in line
    assert "억원" not in line


def test_revenue_and_sales_quantity_still_summarised():
    """정상 경로는 그대로다 — 좁히다가 쓸모를 없애면 안 된다."""
    line = summary([{"Sales1_R": 94799928155, "Total_Qty": 7793099}])
    assert "총 매출액" in line and "948.0억원" in line
    assert "총 판매수량" in line and "7,793,099개" in line


def test_shipment_quantity_is_not_called_sales_quantity():
    """⛔ 판매수량은 `Total_Qty` 뿐이다 — 수출 선적 수량은 다른 값이다."""
    line = summary([{"quantity_ea": 825098}])
    assert "총 수량" in line
    assert "판매수량" not in line


def test_a_real_order_count_is_still_counted():
    line = summary([{"order_count": 42}])
    assert "총 주문" in line and "42건" in line


def test_the_money_test_lives_in_one_place():
    """⛔ 금액 판정은 `answer_check.is_money_column` 한 곳이다.

    여기서 다시 구현하면 교정 후보 고르는 쪽과 언젠가 갈린다.
    """
    import inspect

    from app.agents import sql_agent

    src = inspect.getsource(sql_agent._fast_summary_line)
    assert "is_money_column" in src
    assert "MONEY_TOKENS" not in src, "목록을 여기 다시 적지 마라"


@pytest.mark.parametrize("col", ["total_order_amount", "cost_total_krw", "amount"])
def test_money_columns_never_reach_the_count_branch(col):
    assert "건" not in summary([{col: 1234.5}])


# ── 비중(%)을 금액으로 더하지 않는다 — 2026-09-15 프로덕션 실측 ──────────────
#
#     질문: "2026년 남아메리카 국가별 스킨천사 매출과 비중을 높은 순으로"
#     요약: 총 매출액 약 382.4억원 (38,244,190,784원) · 총 매출액 약 0.0억원 (100원)
#                                                      ^^^^^^^^^^^^^^^^^^^^^^^^^^^
#     뒤엣것은 `revenue_share_pct`(비중, 합이 100)를 금액으로 더한 것이다.
#
# ⚠️ **같은 답변의 표 합계 행은 그 열을 `-` 로 제대로 비웠다.** 판정이 이미 있었는데
#    요약만 그것을 안 보고 컬럼을 직접 훑었다 — "더해도 되는가" 를 두 곳이 따로
#    판정하고 한쪽만 맞는, 이 저장소의 단골 실패다.

_SOUTH_AMERICA = [
    {"Country": "멕시코", "total_revenue": 9905278703, "revenue_share_pct": 25.90},
    {"Country": "브라질", "total_revenue": 28338912081, "revenue_share_pct": 74.10},
]


def test_a_share_column_is_not_summed_as_money():
    line = summary(_SOUTH_AMERICA)

    assert line.count("총 매출액") == 1, f"비중이 금액으로 한 번 더 세어졌다: {line}"
    assert "0.0억원" not in line
    assert "(100원)" not in line
    assert "382.4억원" in line and "38,244,190,784" in line


@pytest.mark.parametrize("col", [
    "revenue_share_pct", "share_pct", "비중", "revenue_share",
    "roas", "avg_cpc", "unit_price", "rank_value", "growth_rate", "ctr",
])
def test_derived_columns_never_reach_the_summary(col):
    """더하면 숫자는 나오지만 뜻이 없는 열 — 요약에 실리면 안 된다."""
    assert summary([{col: 25.9}, {col: 74.1}]) == ""


def test_the_additive_test_lives_in_one_place():
    """⛔ 표 합계와 **같은 함수**로 판정한다 — 갈리면 한쪽만 고쳐진다."""
    import inspect

    from app.agents import sql_agent

    src = inspect.getsource(sql_agent._fast_summary_line)
    assert "_is_additive_metric_column" in src
    assert "_TOTAL_EXCLUDED_TOKENS" not in src, "목록을 여기 다시 적지 마라"


# ── 억 환산도 한 곳에서만 한다 ──────────────────────────────────────────────

def test_an_amount_below_one_eok_is_not_called_zero_eok():
    """⛔ `/1e8` 로 못 박으면 8,287만원이 '약 0.8억원'·'약 0.0억원' 이 된다.

    CLAUDE.md: 1억 미만에 '억' 을 쓰지 마라 (828.7억원 사고가 그 자리다).
    """
    line = summary([{"total_revenue": 40000000}, {"total_revenue": 42871719}])

    assert "8,287만원" in line
    assert "억원" not in line


def test_large_amounts_still_use_eok():
    assert "382.4억원" in summary([{"total_revenue": 38244190784}])


def test_the_amount_conversion_lives_in_one_place():
    import inspect

    from app.agents import sql_agent

    src = inspect.getsource(sql_agent._fast_summary_line)
    assert "render_amount" in src
    assert "100_000_000" not in src and "1e8" not in src, "환산을 여기서 다시 하지 마라"
