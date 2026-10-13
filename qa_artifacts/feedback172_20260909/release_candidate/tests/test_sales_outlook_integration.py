"""Feedback 172: verify actual generation and both chat response paths."""
from datetime import date

import pytest

from app.agents import sql_agent as agent
from app.core import llm


QUESTION = "이번달 전사 예상실적이 얼마로 마감할거 같아?"
OLD_SQL = """WITH current_month_sales AS (
 SELECT SUM(Sales1_R) AS current_revenue,
 COUNT(DISTINCT DATE(Date)) AS elapsed_days
 FROM `skin1004-319714.Sales_Integration.SALES_ALL_Backup`
 WHERE Date BETWEEN '2026-09-01 00:00:00' AND '2026-09-30 23:59:59')
SELECT current_revenue AS revenue_to_date, elapsed_days,
30 AS total_days_in_month,
ROUND(SAFE_DIVIDE(current_revenue, elapsed_days)*30,0) AS estimated_month_end_revenue
FROM current_month_sales LIMIT 1000"""
OLD_ROWS = [{"revenue_to_date": 65655330136.4, "elapsed_days": 16,
             "total_days_in_month": 30, "estimated_month_end_revenue": 123103744006}]
ROWS = [{"outlook_period_start": date(2026, 9, 1),
         "outlook_period_end": date(2026, 9, 30),
         "outlook_as_of": date(2026, 9, 8), "outlook_row_count": 378559,
         "revenue_through_as_of": 12198894347.2,
         "revenue_after_as_of": 53456435789.2,
         "revenue_in_period": 65655330136.4}]


def no_model(*args, **kwargs):
    raise AssertionError("A model must not invent the closing estimate")


def setup_stream(monkeypatch, rows, sql=OLD_SQL):
    monkeypatch.delenv("BQ_TOOL_LOOP", raising=False)
    monkeypatch.delenv("BQ_FAST_ANSWER", raising=False)
    monkeypatch.setattr(agent, "generate_sql", lambda state: {"generated_sql": sql, "error": None})
    monkeypatch.setattr(agent, "validate_sql_node", lambda state: {"sql_valid": True})
    monkeypatch.setattr(agent, "_enforce_partition_filter", lambda sql, *a, **kw: sql)
    monkeypatch.setattr(agent, "execute_sql", lambda state: {"sql_result": rows, "error": None})
    monkeypatch.setattr(agent, "_prepend_data_update_notice", lambda *a: "")
    monkeypatch.setattr(agent, "_allowed_tables_from_sources", lambda *a: None)
    monkeypatch.setattr(llm, "get_flash_client", no_model)


def test_company_outlook_generation_cannot_reuse_model_or_old_cache(monkeypatch):
    monkeypatch.setattr(agent, "get_flash_client", no_model)
    monkeypatch.setattr(agent, "_cache_lookup", no_model)
    state = {"query": QUESTION, "brand_filter": "SK,CBT",
             "conversation_context": "직전 질문: 국내 CS 문의 건수"}
    result = agent.generate_sql(state)
    sql = result["generated_sql"]
    assert result.get("error") is None
    assert "revenue_through_as_of" in sql
    assert "revenue_after_as_of" in sql
    assert "revenue_in_period" in sql
    assert "'SK'" in sql and "'CBT'" in sql and "Brand IN" in sql
    assert "COUNT(DISTINCT" not in sql
    assert "estimated_month_end_revenue" not in sql


def test_total_followup_preserves_previous_country_scope(monkeypatch):
    calls = []
    monkeypatch.setattr(agent, "_build_company_outlook_sql", lambda *a, **kw: calls.append(a))
    monkeypatch.setattr(agent, "get_flash_client", no_model)
    with pytest.raises(AssertionError, match="A model"):
        agent.generate_sql({"query": "이번달 전체 매출 예상 알려줘",
                            "conversation_context": "직전 질문: 일본 매출 알려줘"})
    assert calls == []


@pytest.mark.parametrize("stream", [False, True])
def test_faulty_original_result_never_becomes_a_forecast(monkeypatch, stream):
    setup_stream(monkeypatch, OLD_ROWS)
    monkeypatch.setattr(agent, "get_flash_client", no_model)
    monkeypatch.setattr(agent, "get_llm_client", no_model)
    if stream:
        answer = "".join(agent.run_sql_agent_stream(QUESTION))
    else:
        answer = agent.format_answer({"query": QUESTION, "generated_sql": OLD_SQL,
                                      "sql_result": OLD_ROWS})["answer"]
    body = answer.split("<details>")[0]
    assert "1,231" not in body
    assert "123,103,744" not in body
    assert "16일" not in body
    assert "예상" in body or "마감" in body
    assert "오류" not in body


@pytest.mark.parametrize("mode", ["nonstream", "regular", "fast", "tool"])
def test_registered_current_and_future_are_rendered_without_model(monkeypatch, mode):
    sql = agent._build_company_outlook_sql(QUESTION, today=date(2026, 9, 8))
    setup_stream(monkeypatch, ROWS, sql=sql)
    future_note = agent._future_period_note
    monkeypatch.setattr(agent, "_future_period_note",
                        lambda sql: future_note(sql, today=date(2026, 9, 8)))
    monkeypatch.setattr(agent, "get_flash_client", no_model)
    monkeypatch.setattr(agent, "get_llm_client", no_model)
    if mode == "fast":
        monkeypatch.setenv("BQ_FAST_ANSWER", "1")
        monkeypatch.setattr(agent, "_fast_answer_stream", no_model)
    if mode == "tool":
        monkeypatch.setenv("BQ_TOOL_LOOP", "1")
    if mode != "nonstream":
        answer = "".join(agent.run_sql_agent_stream(QUESTION))
    else:
        answer = agent.format_answer({"query": QUESTION, "generated_sql": sql,
                                      "sql_result": ROWS})["answer"]
    body = answer.split("<details>")[0]
    assert "122.0" in body and "534.6" in body and "656.6" in body
    assert "2026-09-08" in body and "2026-09-30" in body
    assert "2026-09-09 ~ 2026-09-30" in body
    assert "2026-10-01" not in body  # Exclusive SQL boundary is outside the reported month.
    assert "1,231" not in body and "16일간" not in body
    assert "Sales1_R" not in body and "SALES_ALL_Backup" not in body


@pytest.mark.parametrize("fast", [False, True])
def test_live_stream_discloses_future_dates_even_in_fast_mode(monkeypatch, fast):
    sql = ("SELECT SUM(Sales1_R) AS revenue FROM sales "
           "WHERE Date BETWEEN '2090-01-01' AND '2090-01-31'")
    setup_stream(monkeypatch, [{"revenue": 10}], sql=sql)
    if fast:
        monkeypatch.setenv("BQ_FAST_ANSWER", "1")
        monkeypatch.setattr(agent, "_fast_answer_stream", lambda *a, **kw: iter(["REGULAR_BODY"]))
    else:
        class Formatter:
            def generate_stream(self, *a, **kw):
                return iter(["REGULAR_BODY"])
        monkeypatch.setattr(agent, "get_flash_client", lambda: Formatter())
        monkeypatch.setattr(llm, "get_flash_client", lambda: Formatter())
        monkeypatch.setattr(agent, "_try_generate_chart", lambda *a: "")
        monkeypatch.setattr(agent, "_number_check_notice", lambda *a: "")
        monkeypatch.setattr(agent, "_attach_full_data_download", lambda *a: "")
    answer = "".join(agent.run_sql_agent_stream("2090년 1월 매출"))
    body = answer.split("<details>")[0]
    assert body.count("아직 일어나지 않은") == 1
    assert body.index("아직 일어나지 않은") < body.index("REGULAR_BODY")


def test_deterministic_company_sql_still_obeys_disabled_source(monkeypatch):
    monkeypatch.setattr(agent, "get_flash_client", no_model)
    generated = agent.generate_sql({"query": QUESTION})
    monkeypatch.setattr(agent, "_allowed_tables_from_sources", lambda *a: set())
    monkeypatch.setattr(agent, "get_bigquery_client", no_model)
    result = agent.execute_sql({"query": QUESTION, **generated, "sql_valid": True,
                                "enabled_sources": []})
    assert result.get("error")
    assert result.get("sql_result") is None


def test_verified_feedback_resolution_targets_exact_report():
    from app.core.feedback_inbox import DEPLOYED_FEEDBACK_RESOLUTIONS
    matching = [entry for entry in DEPLOYED_FEEDBACK_RESOLUTIONS if entry["id"] == 172]
    assert len(matching) == 1
    assert matching[0]["created_on"] == "2026-09-08"
    assert "등록" in matching[0]["note"]
