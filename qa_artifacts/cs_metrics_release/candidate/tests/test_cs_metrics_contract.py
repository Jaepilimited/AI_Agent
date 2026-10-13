"""CS의 행 단위·중첩 스키마·코드값·모든 답변 경로 계약."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.core.cs_metrics import (
    DOMESTIC_TABLE as D, OVERSEAS_TABLE as O, normalize_sql, validation_error,
    notice, answer_fact, prepare_results,
)


@pytest.mark.parametrize("predicate, expected", [
    ("claim_type='반품'", "claim_type='return'"),
    ("d.claim_type IN ('반품', '교환', '재배송')", "d.claim_type IN ('return', 'exchange', 'redelivery')"),
    ("process_status = '처리 완료'", "process_status = 'completed'"),
    ("`process_status` NOT IN ('진행중', '취소')", "`process_status` NOT IN ('in_progress', 'cancelled')"),
    ("claim_type = '미확인유형'", "claim_type = '미확인유형'"),
])
def test_domestic_codes_cannot_silently_return_zero(predicate, expected):
    sql = f"SELECT COUNT(*) FROM `{D}` d WHERE {predicate}"
    assert normalize_sql(sql) == f"SELECT COUNT(*) FROM `{D}` d WHERE {expected}"


@pytest.mark.parametrize("predicate, expected", [
    ("country_name='미국'", "country_name='United States'"),
    ("r.country_name IN ('미국','스페인','멕시코')", "r.country_name IN ('United States','Spain','Mexico')"),
    ("country_name != '영국'", "country_name != 'United Kingdom'"),
    ("process_status='완료'", "process_status='completed'"),
])
def test_overseas_uses_english_country_and_status_codes(predicate, expected):
    sql = f"SELECT COUNT(*) FROM `{O}` r WHERE {predicate}"
    assert normalize_sql(sql) == f"SELECT COUNT(*) FROM `{O}` r WHERE {expected}"


def test_double_quoted_bigquery_literals_and_short_table_names():
    sql = 'SELECT COUNT(*) FROM `cs_dashboard.overseas_cs_refunds` WHERE country_name="미국" AND process_status IN ("완료","진행중")'
    out = normalize_sql(sql)
    assert O in out and 'country_name="United States"' in out
    assert 'IN ("completed","in_progress")' in out
    assert "Shopify" in notice(sql)
    from app.core.security import validate_sql
    assert validate_sql(sql, {D})[0] is False
    assert validate_sql(sql, {O})[0] is True


def test_other_tables_and_returned_labels_are_untouched():
    sql = "SELECT '완료' AS status FROM `p.d.other` WHERE process_status='완료'"
    assert normalize_sql(sql) == sql
    cs_sql = f"SELECT 'process_status = ''완료''' AS example, '미국' AS label FROM `{O}`"
    assert normalize_sql(cs_sql) == cs_sql
    assert notice(sql) == ""
    assert answer_fact(sql) == ""


@pytest.mark.parametrize("amount", ["payment_amount_usd", "r.payment_amount_krw", "`payment_amount_usd`"])
def test_repeated_order_payment_cannot_be_summed_from_refund_rows(amount):
    sql = f"SELECT SUM({amount}) AS payment FROM `{O}` r"
    assert "중복" in validation_error(sql)
    from app.core.security import validate_sql
    assert validate_sql(sql, {O})[0] is False


def test_order_dedup_and_refund_sum_are_allowed():
    dedup = f"""WITH orders AS (
        SELECT shopify_order_id, MAX(payment_amount_usd) AS amount
        FROM `{O}` GROUP BY shopify_order_id)
        SELECT SUM(amount) AS payment_usd FROM orders"""
    assert validation_error(dedup) == ""
    assert validation_error(f"SELECT SUM(refund_amount_usd) FROM `{O}`") == ""


@pytest.mark.parametrize("fn", ["COALESCE", "IFNULL"])
def test_missing_fx_is_not_zero(fn):
    assert "0원" in validation_error(f"SELECT SUM({fn}(refund_amount_krw, 0)) FROM `{O}`")
    assert validation_error(f"SELECT SUM(refund_amount_krw), COUNTIF(refund_amount_krw IS NULL) FROM `{O}`") == ""


@pytest.mark.parametrize("expr", ["SUM(IFNULL(`refund_amount_krw`, 0))", "IFNULL(SUM(refund_amount_krw), 0)", "SUM(DISTINCT payment_amount_usd)"])
def test_guard_covers_quoted_null_amounts_and_distinct_amount_shortcut(expr):
    assert validation_error(f"SELECT {expr} FROM `{O}`")


def test_scope_and_limitations_are_attached_only_to_relevant_data():
    d = notice(f"SELECT SUM(eligible_recorded_compensation) FROM `{D}`")
    assert "처리 기록" in d and "실제 지급 확인액이 아닙니다" in d
    o = notice(f"SELECT return_type_normalized, SUM(refund_amount_krw) FROM `{O}`")
    assert "전체 해외 CS 접수" in o and "0원이 아닙니다" in o and "미기재" in o
    assert "보상액" not in o


def test_nested_schema_preserves_array_and_child_types():
    from app.core.bigquery import BigQueryClient
    from google.cloud.bigquery import SchemaField

    client = object.__new__(BigQueryClient)
    client.client = Mock()
    client.client.get_table.return_value = SimpleNamespace(schema=[
        SchemaField("items", "RECORD", mode="REPEATED", fields=[
            SchemaField("quantity", "INTEGER", description="접수 상품수량"),
            SchemaField("barcode", "STRING"),
        ]),
        SchemaField("inspections", "RECORD", mode="REPEATED", fields=[SchemaField("quantity", "INTEGER")]),
    ])
    by_name = {row["name"]: row for row in client.get_table_schema(D)}
    assert by_name["items"]["mode"] == "REPEATED"
    assert by_name["items.quantity"]["type"] == "INTEGER"
    assert by_name["items.quantity"]["description"] == "접수 상품수량"
    assert "inspections.quantity" in by_name


def test_lazy_schema_for_past_feedback_and_selected_source(monkeypatch):
    from app.agents import sql_agent as agent
    monkeypatch.setattr(agent, "_schema_cache_tables", {D: "DOMESTIC_SCHEMA", O: "OVERSEAS_SCHEMA"})
    monkeypatch.setattr(agent, "get_bigquery_client", Mock())
    assert "OVERSEAS_SCHEMA" in agent._build_schema_context("이번 달 글로벌 자사몰 CS 인입 건수 알려줘", {O})
    assert "DOMESTIC_SCHEMA" in agent._build_schema_context("채널별로 알려줘", {D})
    assert "OVERSEAS_SCHEMA" not in agent._build_schema_context("국내 CS 건수", {D})


def test_usd_and_count_totals_are_not_labelled_as_krw():
    from app.agents.sql_agent import _amount_note
    rows = [{"refund_count": 10, "refund_amount_usd": 200.5}, {"refund_count": 3, "refund_amount_usd": 40}]
    text = _amount_note(rows, f"SELECT country_name, COUNT(*), SUM(refund_amount_usd) FROM `{O}`")
    assert "USD 240.50" in text
    assert "240원" not in text and "13원" not in text


def test_fx_missing_count_is_not_money_even_with_krw_alias():
    from app.agents.sql_agent import _amount_note
    rows = [{"total_refund_amount_usd": 100, "refunds_without_krw": 10},
            {"total_refund_amount_usd": 50, "refunds_without_krw": 1}]
    sql = f"SELECT country_name, SUM(refund_amount_usd) AS total_refund_amount_usd, COUNTIF(refund_amount_krw IS NULL) AS refunds_without_krw FROM `{O}` GROUP BY country_name"
    text = _amount_note(rows, sql)
    assert "USD 150.00" in text and "11원" not in text


def test_distinct_orders_across_months_are_not_summed_by_any_renderer():
    from app.agents.sql_agent import _build_table_totals_markdown, _fast_summary_line, _fast_table_markdown
    sql = f"SELECT month, COUNT(DISTINCT shopify_order_id) AS distinct_orders FROM `{O}` GROUP BY month"
    rows = prepare_results(sql, [{"month": "2026-08", "distinct_orders": 1}, {"month": "2026-09", "distinct_orders": 1}])
    assert _build_table_totals_markdown(rows) == ""
    assert _fast_summary_line(rows) == ""
    assert "**합계**" not in _fast_table_markdown(rows)


def test_raw_compensation_does_not_claim_exclusions():
    text = notice(f"SELECT SUM(compensation_amount) FROM `{D}`")
    assert "포함될 수 있습니다" in text
    assert "완료·비철회·검토 제외 기준" not in text


def test_distinct_sql_alias_and_additive_refunds_keep_different_rules():
    from app.agents.sql_agent import _additive_totals, _fast_table_markdown
    sql = f"SELECT month, COUNT(DISTINCT shopify_order_id) AS order_count, SUM(refund_amount_usd) AS total_refund_amount_usd FROM `{O}` GROUP BY month"
    rows = prepare_results(sql, [{"month": "8월", "order_count": 1, "total_refund_amount_usd": 10},
                                 {"month": "9월", "order_count": 1, "total_refund_amount_usd": 20}])
    assert _additive_totals(rows)[1] == {"total_refund_amount_usd": 30}
    table = _fast_table_markdown(rows)
    assert "USD" in table and "**2**" not in table and "**30**" in table


def test_fast_title_does_not_call_refunds_all_intake():
    from app.core.cs_metrics import answer_title
    assert answer_title("이번 달 글로벌 자사몰 CS 인입 건수", f"SELECT COUNT(*) FROM `{O}`") == "해외 CS 환불 기록 조회"


def test_empty_cs_result_preserves_domain_without_sales_suggestions():
    from app.agents.sql_agent import format_answer
    text = format_answer({"query": "국내 CS 내역", "generated_sql": f"SELECT channel FROM `{D}` WHERE FALSE", "sql_result": []})["answer"]
    assert "처리 기록" in text and "조회되지 않았습니다" in text
    assert "매출" not in text and "라자다" not in text


def test_every_answer_path_carries_cs_scope_and_interpretation():
    from tests._answer_paths import assert_every_answer_path_has, function_body
    assert_every_answer_path_has("_cs_notice(sql, results)", "환불 건수를 전체 CS로 부르지 않는다")
    assert_every_answer_path_has("_cs_answer_fact(sql)", "표와 인사이트가 같은 정의를 쓴다")
    assert "_cs_notice(unlimited_sql, results)" in function_body("run_sql_agent_unlimited")
    assert "_prepare_cs_results(unlimited_sql, results)" in function_body("run_sql_agent_unlimited")


def test_fi_masking_keeps_both_cs_schema_sections(monkeypatch):
    from app.agents import sql_agent as agent
    from app.core import value_lists
    monkeypatch.setattr(value_lists, "fill", lambda text: text)
    monkeypatch.setattr(agent, "_prompt_cache", {})
    prompt = agent._load_prompt("sql_generator.txt", can_view_fi=False)
    assert D in prompt and O in prompt
    assert "## 테이블 14: FI_LLM_Flat" not in prompt
