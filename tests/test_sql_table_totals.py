"""Deterministic totals for BigQuery result tables.

These totals must not depend on the answer-writing LLM: the thumbs-down case
that motivated this suite returned a multi-row data table without a sum.
"""

from decimal import Decimal


def test_total_block_sums_sales_quantity_and_marketing_counts():
    from app.agents.sql_agent import _build_table_totals_markdown

    rows = [
        {
            "country": "일본",
            "total_revenue": Decimal("100000000"),
            "total_qty": 10,
            "cost_krw": 30,
            "impressions": 1_000,
            "clicks": 50,
            "ctr": 0.05,
            "roas": 3.2,
        },
        {
            "country": "미국",
            "total_revenue": Decimal("250000000"),
            "total_qty": 25,
            "cost_krw": 70,
            "impressions": 2_000,
            "clicks": 100,
            "ctr": 0.05,
            "roas": 4.1,
        },
    ]

    block = _build_table_totals_markdown(rows)

    assert "#### 합계" in block
    assert "350,000,000" in block
    assert "35" in block
    assert "3,000" in block
    assert "150" in block
    assert "CTR" not in block
    assert "ROAS" not in block


def test_total_block_uses_all_rows_not_only_visible_preview():
    from app.agents.sql_agent import _build_table_totals_markdown

    rows = [{"product": f"P{i}", "total_quantity": i} for i in range(1, 21)]

    block = _build_table_totals_markdown(rows)

    assert "전체 20행 기준" in block
    assert "210" in block


def test_total_block_does_not_double_count_existing_total_row():
    from app.agents.sql_agent import _build_table_totals_markdown

    rows = [
        {"country": "일본", "sales": 100},
        {"country": "미국", "sales": 200},
        {"country": "합계", "sales": 300},
    ]

    block = _build_table_totals_markdown(rows)

    assert "300" in block
    assert "600" not in block


def test_total_block_skips_non_additive_metrics():
    from app.agents.sql_agent import _build_table_totals_markdown

    rows = [
        {"month": "2026-01", "ctr": 0.1, "roas": 3.0, "avg_cpc": 120.0},
        {"month": "2026-02", "ctr": 0.2, "roas": 4.0, "avg_cpc": 100.0},
    ]

    assert _build_table_totals_markdown(rows) == ""


def test_total_classifier_handles_business_aliases_without_summing_ids_or_unit_costs():
    from app.agents.sql_agent import _build_table_totals_markdown

    rows = [
        {"team": "A", "order_id": 1001, "cost_per_click": 120.0,
         "cart_adds": 4, "op_income": 30},
        {"team": "B", "order_id": 1002, "cost_per_click": 100.0,
         "cart_adds": 6, "op_income": 20},
    ]

    block = _build_table_totals_markdown(rows)

    assert "장바구니 추가" in block and "10" in block
    assert "영업이익" in block and "50" in block
    assert "cart_adds" not in block
    assert "op_income" not in block
    assert "order_id" not in block
    assert "cost_per_click" not in block


def test_feedback_151_monthly_ad_cost_totals_keep_month_labels_distinct():
    """The registered thumbs-down compares July/August team ad spend."""
    from app.agents.sql_agent import _build_table_totals_markdown

    rows = [
        {"team": "EAST1", "july_ad_cost": 200, "august_ad_cost": 120,
         "change_amount": -80, "change_rate_pct": -40.0},
        {"team": "JBT", "july_ad_cost": 100, "august_ad_cost": 80,
         "change_amount": -20, "change_rate_pct": -20.0},
    ]

    block = _build_table_totals_markdown(rows)

    assert "7월 광고비 (원)" in block and "**300**" in block
    assert "8월 광고비 (원)" in block and "**200**" in block
    assert "증감액 (원)" in block and "**-100**" in block
    assert "change_rate_pct" not in block


def test_fast_table_contains_a_total_row_for_additive_columns():
    from app.agents.sql_agent import _fast_table_markdown

    table = _fast_table_markdown([
        {"country": "일본", "total_revenue": 100, "ctr": 0.1},
        {"country": "미국", "total_revenue": 200, "ctr": 0.2},
    ])

    assert "| **합계** | **300** | - |" in table


def test_streaming_sql_answer_always_appends_grounded_total(monkeypatch):
    from app.agents import sql_agent

    rows = [
        {"country": "일본", "total_revenue": 100},
        {"country": "미국", "total_revenue": 200},
    ]

    class FakeLlm:
        def generate_stream(self, *args, **kwargs):
            yield "### 결과\n\n| 국가 | 매출 |\n|---|---:|\n| 일본 | 100 |\n| 미국 | 200 |"

    monkeypatch.delenv("BQ_TOOL_LOOP", raising=False)
    monkeypatch.delenv("BQ_FAST_ANSWER", raising=False)
    monkeypatch.setattr(sql_agent, "generate_sql", lambda state: {"generated_sql": "SELECT 1"})
    monkeypatch.setattr(sql_agent, "validate_sql_node", lambda state: {"sql_valid": True})
    monkeypatch.setattr(sql_agent, "_enforce_partition_filter", lambda sql, *a, **kw: sql)
    monkeypatch.setattr(sql_agent, "execute_sql", lambda state: {"sql_result": rows})
    monkeypatch.setattr(sql_agent, "get_flash_client", lambda: FakeLlm())
    monkeypatch.setattr(sql_agent, "_try_generate_chart", lambda *a, **kw: "")
    monkeypatch.setattr("app.core.llm.get_flash_client", lambda: FakeLlm())

    answer = "".join(sql_agent.run_sql_agent_stream("국가별 매출"))

    assert "#### 합계" in answer
    assert "300" in answer


def test_non_streaming_sql_answer_inserts_grounded_total_before_analysis(monkeypatch):
    from app.agents import sql_agent

    class FakeLlm:
        def generate(self, *args, **kwargs):
            return "### 결과\n\n#### 상세 데이터 (표)\n표\n\n#### 분석 및 인사이트\n- 분석"

    rows = [
        {"country": "일본", "total_revenue": 100},
        {"country": "미국", "total_revenue": 200},
    ]
    monkeypatch.setattr(sql_agent, "get_flash_client", lambda: FakeLlm())
    monkeypatch.setattr(sql_agent, "_try_generate_chart", lambda *a, **kw: "")

    answer = sql_agent.format_answer({
        "query": "국가별 매출",
        "generated_sql": "SELECT country, SUM(Sales1_R) AS total_revenue FROM data GROUP BY country",
        "sql_result": rows,
        "error": None,
    })["answer"]

    assert "#### 합계" in answer
    assert "300" in answer
    assert answer.index("#### 합계") < answer.index("#### 분석 및 인사이트")


def test_stream_total_insertion_handles_analysis_heading_split_across_chunks():
    from app.agents.sql_agent import _stream_with_table_totals

    chunks = ["#### 상세 데이터\n| 표 |\n", "#### 분", "석 및 인사이트\n- 내용"]
    totals = "#### 합계\n| 매출 | 300 |"

    answer = "".join(_stream_with_table_totals(chunks, totals))

    assert answer.index("#### 상세 데이터") < answer.index("#### 합계")
    assert answer.index("#### 합계") < answer.index("#### 분석 및 인사이트")
    assert answer.count("#### 합계") == 1
