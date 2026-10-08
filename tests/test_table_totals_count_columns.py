# -*- coding: utf-8 -*-
"""건수 합계가 합계표에서 조용히 빠지지 않는다 — 붐따 #185 (2026-10-07 17:08).

제보 그대로: *"합계 부분에 환불액 합계는 표기되나 건수 합계는 표기되지 않음"*

해외 CS 환불 표의 열은 이랬다 (프로덕션 SQL 실측):

    COUNT(*)                         AS refund_records          <- 빠졌다
    COUNT(DISTINCT order_id)         AS distinct_orders         <- 빠져야 맞다
    SUM(refund_amount_usd)           AS total_refund_amount_usd <- 더해졌다
    SUM(refund_amount_krw)           AS total_refund_amount_krw <- 더해졌다
    COUNTIF(refund_amount_krw IS NULL) AS refunds_without_krw   <- 빠졌다

⛔ 원인은 **이름으로 판정**한 것이다. `_TOTAL_ADDITIVE_TOKENS` 에 `records` 가
   없어 `refund_records` 가 지표로 안 보였다. 바로 옆 국내 표는 `record_count` 라
   `count` 토큰에 **우연히** 걸려 더해졌다 — 우연에 기대고 있었다.

⛔ 그리고 금액 줄만 있는 합계표는 **그럴듯하다**. 사람은 "건수는 합계를 낼 수 없는
   값인가" 로 읽고 넘어간다 (에러가 아니라 조용한 결손).

보증은 **선언**이다 — SQL 이 `COUNT(...) AS 별칭` 으로 센 열을 그대로 믿는다.
이름 목록을 늘리는 방식으로는 끝나지 않는다 (records·cases·tickets·claims…).
"""

from app.agents.sql_agent import (
    _additive_totals,
    _build_table_totals_markdown,
    _is_additive_metric_column,
    _name_excludes_total,
)
from app.core.cs_metrics import OVERSEAS_TABLE, prepare_results

# 제보된 그 SQL 의 선언부 (별칭이 같아야 회귀가 뜻을 갖는다)
SQL = f"""
SELECT FORMAT_DATE('%Y-%m', refunded_at) AS month,
       COUNT(*) AS refund_records,
       COUNT(DISTINCT order_id) AS distinct_orders,
       SUM(refund_amount_usd) AS total_refund_amount_usd,
       SUM(refund_amount_krw) AS total_refund_amount_krw,
       COUNTIF(refund_amount_krw IS NULL) AS refunds_without_krw
FROM `{OVERSEAS_TABLE}`
GROUP BY month ORDER BY month
"""

ROWS = [
    {"month": "2026-07", "refund_records": 25, "distinct_orders": 24,
     "total_refund_amount_usd": 1000.5, "total_refund_amount_krw": 1350000,
     "refunds_without_krw": 0},
    {"month": "2026-08", "refund_records": 30, "distinct_orders": 28,
     "total_refund_amount_usd": 1200.25, "total_refund_amount_krw": 1620000,
     "refunds_without_krw": 0},
    {"month": "2026-09", "refund_records": 25, "distinct_orders": 25,
     "total_refund_amount_usd": 900.0, "total_refund_amount_krw": 1215000,
     "refunds_without_krw": 0},
]


def _totals(sql=SQL, rows=None):
    return _additive_totals(prepare_results(sql, list(rows or ROWS)))[1]


# ── 제보된 표가 실제로 고쳐졌는가 ─────────────────────────────────────────────

def test_the_reported_count_columns_are_totalled():
    totals = _totals()

    assert totals["refund_records"] == 80, "환불 건수 합계가 빠졌다 (붐따 #185)"
    assert totals["refunds_without_krw"] == 0, "원화 미환산 건수 합계가 빠졌다"


def test_the_amount_columns_still_total_as_before():
    """⚠️ 고치면서 원래 되던 것이 깨지지 않았는지 — 금액은 그대로 더해진다."""
    totals = _totals()

    assert round(totals["total_refund_amount_usd"], 2) == 3100.75
    assert totals["total_refund_amount_krw"] == 4185000


def test_the_distinct_count_is_still_excluded():
    """⛔ `COUNT(DISTINCT …)` 는 축 사이에서 같은 주문이 재등장해 더하면 틀린다."""
    totals = _totals()

    assert "distinct_orders" not in totals
    assert 77 not in totals.values(), "고유 주문 수가 더해졌다"


def test_the_totals_table_shows_the_count_line():
    md = _build_table_totals_markdown(prepare_results(SQL, list(ROWS)))

    assert md, "합계 블록이 통째로 비었다"
    assert "80" in md, "건수 합계가 화면에 없다 — 제보된 그 증상이다"
    assert "77" not in md, "고유 주문 수가 화면 합계에 올라왔다"


# ── 이름이 아니라 선언으로 판정한다 ──────────────────────────────────────────

def test_the_name_alone_would_still_miss_it():
    """이 테스트가 깨지면 이름 목록으로 때운 것이다 — 그 방식은 끝나지 않는다.

    (이름 목록을 늘려도 좋지만, **선언 경로가 보증**이라는 것이 이 방어의 전부다.)
    """
    assert not _is_additive_metric_column("refund_records")
    assert not _is_additive_metric_column("refunds_without_krw")


def test_the_declaration_travels_with_every_query_not_only_cs():
    """⛔ CS 질문에만 달면 같은 결손이 다른 표에서 그대로 재발한다."""
    sql = "SELECT country, COUNT(*) AS shipment_records FROM `p.d.other` GROUP BY country"
    rows = [{"country": "미국", "shipment_records": 7},
            {"country": "일본", "shipment_records": 5}]

    prepared = prepare_results(sql, rows)
    assert "shipment_records" in getattr(prepared, "count_columns", set())
    assert _additive_totals(prepared)[1]["shipment_records"] == 12


def test_a_declared_count_does_not_beat_an_excluded_name():
    """⛔ 선언이 제외 이름을 이기면 이 방어가 거꾸로 선다 — 비율까지 더해진다."""
    sql = ("SELECT month, COUNT(*) AS conversion_rate, COUNT(*) AS refund_records "
           "FROM `%s` GROUP BY month" % OVERSEAS_TABLE)
    rows = [{"month": "2026-08", "conversion_rate": 3, "refund_records": 3},
            {"month": "2026-09", "conversion_rate": 4, "refund_records": 4}]

    totals = _additive_totals(prepare_results(sql, rows))[1]

    assert "conversion_rate" not in totals
    assert totals["refund_records"] == 7


def test_excluded_names_are_judged_in_one_place():
    """제외 목록을 읽는 곳이 둘이 되면 한쪽만 고쳐진다 (이 저장소의 단골 함정)."""
    for name in ("conversion_rate", "avg_refund", "order_id", "received_date",
                 "환불비율", "평균환불액"):
        assert _name_excludes_total(name), name
    for name in ("refund_records", "total_refund_amount_usd", "shipment_records"):
        assert not _name_excludes_total(name), name


# ── 통화가 섞여도 건수는 더한다 ───────────────────────────────────────────────

def test_counts_survive_a_mixed_currency_axis_but_money_does_not():
    """⚠️ 통화 축에서 빼는 것은 **금액**뿐이다. `refunds_without_krw` 처럼 이름에
    통화가 들어간 **건수**가 실제로 있다 — 그것까지 빼면 이 결손이 되살아난다."""
    sql = ("SELECT currency, COUNT(*) AS refund_records, "
           "COUNTIF(refund_amount_krw IS NULL) AS refunds_without_krw, "
           "SUM(refund_amount_usd) AS total_refund_amount_usd "
           "FROM `%s` GROUP BY currency" % OVERSEAS_TABLE)
    rows = [{"currency": "USD", "refund_records": 40, "refunds_without_krw": 1,
             "total_refund_amount_usd": 1000.0},
            {"currency": "EUR", "refund_records": 10, "refunds_without_krw": 2,
             "total_refund_amount_usd": 300.0}]

    totals = _additive_totals(prepare_results(sql, rows))[1]

    assert totals["refund_records"] == 50
    assert totals["refunds_without_krw"] == 3
    assert "total_refund_amount_usd" not in totals, "통화가 섞인 금액을 더했다"


def test_an_empty_result_is_still_returned_untouched():
    assert prepare_results(SQL, []) == []
