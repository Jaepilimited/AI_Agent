from pathlib import Path

import pytest

from app.agents.orchestrator import (
    OrchestratorAgent,
    _build_conversation_context,
    _should_continue_bigquery_for_correction,
)
from app.agents.sql_agent import (
    execute_sql,
    _has_current_date_cap,
    _has_partitioned_period_ranking,
    _requires_current_date_cap,
    _requires_partitioned_period_ranking,
)
from app.core.term_aliases import _fuzzy_correct


INCIDENT_QUERY = (
    "월별 각 b2b 업체별 매출의 비중이 얼마인지 2025년부터 뽑아줘. "
    "왜냐면 좀 비중있는 업체별의 매출이 대충 얼마인지 확인하려해"
)
CORRECTION_QUERY = "내가 2025년부터라고 했지. 그리고 포어마이징은 언급도 안했어"
PREVIOUS_SQL = """SELECT Company_Name, SUM(Sales1_R) AS revenue
FROM `skin1004-319714.Sales_Integration.SALES_ALL_Backup`
WHERE Date >= '2025-01-01' AND Sales_Type = 'B2B'
GROUP BY Company_Name
LIMIT 1000"""


def _conversation_messages():
    return [
        {"role": "user", "content": INCIDENT_QUERY},
        {
            "role": "assistant",
            "content": (
                "### 2025년 B2B 업체별 매출\n"
                "<details><summary>실행된 쿼리</summary>\n\n"
                f"```sql\n{PREVIOUS_SQL}\n```\n</details>"
            ),
        },
        {"role": "user", "content": CORRECTION_QUERY},
    ]


def test_fuzzy_alias_does_not_invent_poremizing_from_question_word():
    corrected, hits = _fuzzy_correct(INCIDENT_QUERY)

    assert corrected == INCIDENT_QUERY
    assert hits == []
    assert "포어마이징" not in corrected


def test_fuzzy_alias_keeps_high_confidence_product_typo_correction():
    corrected, hits = _fuzzy_correct("포어마징 매출 알려줘")

    assert corrected.startswith("포어마이징(포어마징)")
    assert hits


def test_bigquery_correction_requires_previous_sql_anchor():
    context = _build_conversation_context(_conversation_messages())

    assert "[직전 실행 SQL" in context
    assert _should_continue_bigquery_for_correction(CORRECTION_QUERY, context)
    assert not _should_continue_bigquery_for_correction(CORRECTION_QUERY, "AI: 제품 설명")
    assert not _should_continue_bigquery_for_correction(
        "포어마이징 앰플 사용법 알려줘", context
    )


@pytest.mark.asyncio
async def test_nonstream_correction_continues_previous_bigquery(monkeypatch):
    agent = OrchestratorAgent()
    called = {"bigquery": False}

    async def fake_report(*args, **kwargs):
        return None

    async def fake_bigquery(*args, **kwargs):
        called["bigquery"] = True
        return {"source": "bigquery", "sentinel": "corrected-query"}

    monkeypatch.setattr("app.core.term_aliases._load", lambda: [])
    monkeypatch.setattr(agent, "_handle_report", fake_report)
    monkeypatch.setattr(agent, "_handle_bigquery", fake_bigquery)

    result = await agent.route_and_execute(
        CORRECTION_QUERY,
        messages=_conversation_messages(),
    )

    assert called["bigquery"] is True
    assert result == {"source": "bigquery", "sentinel": "corrected-query"}


@pytest.mark.asyncio
async def test_stream_correction_emits_bigquery_before_cs(monkeypatch):
    agent = OrchestratorAgent()
    monkeypatch.setattr("app.core.term_aliases._load", lambda: [])

    stream = agent.route_and_stream(
        CORRECTION_QUERY,
        messages=_conversation_messages(),
    )
    first_event = await anext(stream)
    await stream.aclose()

    assert first_event == ("source", "bigquery")


def test_sql_prompt_prevents_global_limit_from_truncating_requested_period():
    prompt_path = Path(__file__).parents[1] / "prompts" / "sql_generator.txt"
    prompt = prompt_path.read_text(encoding="utf-8")

    assert "기간 × 고카디널리티 교차분석" in prompt
    assert "ROW_NUMBER() OVER (PARTITION BY month" in prompt
    assert "전역 LIMIT 때문에 기간이" in prompt


def test_period_rank_gate_uses_previous_context_for_correction():
    context = _build_conversation_context(_conversation_messages())

    assert _requires_partitioned_period_ranking(INCIDENT_QUERY)
    assert _requires_partitioned_period_ranking(CORRECTION_QUERY, context)
    assert not _requires_partitioned_period_ranking("2025년 월별 전체 B2B 매출")

    unsafe = "SELECT month, company FROM sales ORDER BY month LIMIT 1000"
    unsafe_rank_only = (
        "SELECT month, company, "
        "ROW_NUMBER() OVER (PARTITION BY month ORDER BY revenue DESC) AS rank_in_month "
        "FROM sales ORDER BY month LIMIT 1000"
    )
    safe = (
        "SELECT * FROM ranked WHERE rank_in_month <= 10 "
        "QUALIFY ROW_NUMBER() OVER (PARTITION BY month ORDER BY revenue DESC) <= 10"
    )
    assert not _has_partitioned_period_ranking(unsafe)
    assert not _has_partitioned_period_ranking(unsafe_rank_only)
    assert _has_partitioned_period_ranking(safe)


def test_historical_since_query_excludes_future_rows_in_followup_context():
    context = _build_conversation_context(_conversation_messages())

    assert _requires_current_date_cap(INCIDENT_QUERY)
    assert _requires_current_date_cap(CORRECTION_QUERY, context)
    assert not _requires_current_date_cap("2027년 예상 매출 전망")
    assert not _has_current_date_cap(
        "SELECT * FROM sales WHERE Date >= '2025-01-01'"
    )
    assert _has_current_date_cap(
        "SELECT * FROM sales WHERE Date >= '2025-01-01' "
        "AND Date <= CURRENT_DATETIME()"
    )
    assert _has_current_date_cap(
        "SELECT * FROM sales WHERE Date BETWEEN '2025-01-01' AND '2026-08-14'"
    )


def test_grouped_window_bigquery_error_retries_with_context(monkeypatch):
    context = _build_conversation_context(_conversation_messages())
    bad_sql = """SELECT
      FORMAT_DATETIME('%Y-%m', Date) AS month,
      Company_Name,
      SUM(Sales1_R) AS revenue,
      ROW_NUMBER() OVER (
        PARTITION BY FORMAT_DATETIME('%Y-%m', Date)
        ORDER BY SUM(Sales1_R) DESC
      ) AS rank_in_month
    FROM `skin1004-319714.Sales_Integration.SALES_ALL_Backup`
    WHERE Date >= '2025-01-01' AND Sales_Type = 'B2B'
    GROUP BY month, Company_Name
    LIMIT 1000"""
    safe_sql = """WITH monthly AS (
      SELECT FORMAT_DATETIME('%Y-%m', Date) AS month, Company_Name,
             SUM(Sales1_R) AS revenue
      FROM `skin1004-319714.Sales_Integration.SALES_ALL_Backup`
      WHERE Date >= '2025-01-01' AND Date <= CURRENT_DATETIME()
        AND Sales_Type = 'B2B'
      GROUP BY month, Company_Name
    ), ranked AS (
      SELECT *, ROW_NUMBER() OVER (
        PARTITION BY month ORDER BY revenue DESC
      ) AS rank_in_month
      FROM monthly
    )
    SELECT * FROM ranked WHERE rank_in_month <= 10 LIMIT 1000"""
    prompts = []

    class FakeBigQuery:
        def __init__(self):
            self.calls = 0

        def execute_query(self, sql, **kwargs):
            self.calls += 1
            if self.calls == 1:
                raise Exception(
                    "PARTITION BY expression references column Date which is neither "
                    "grouped nor aggregated"
                )
            assert sql == safe_sql
            return [{"month": "2025-01", "rank_in_month": 1}]

    class FakeLLM:
        def generate(self, prompt, **kwargs):
            prompts.append(prompt)
            return safe_sql

    fake_bq = FakeBigQuery()
    monkeypatch.setattr("app.agents.sql_agent.get_bigquery_client", lambda: fake_bq)
    monkeypatch.setattr("app.agents.sql_agent.get_flash_client", lambda: FakeLLM())
    monkeypatch.setattr("app.agents.sql_agent._build_schema_context", lambda *a, **k: "")

    result = execute_sql({
        "query": CORRECTION_QUERY,
        "conversation_context": context,
        "brand_filter": None,
        "enabled_sources": None,
        "can_view_fi": False,
        "generated_sql": bad_sql,
        "sql_valid": True,
    })

    assert result["generated_sql"] == safe_sql
    assert result["sql_result"] == [{"month": "2025-01", "rank_in_month": 1}]
    assert fake_bq.calls == 2
    assert prompts and INCIDENT_QUERY in prompts[0]
    assert "첫 CTE에서 기간·항목별 집계" in prompts[0]
