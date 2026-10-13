"""Order details must not reuse a cached projection that omitted invoices."""
from datetime import date
import re
from types import SimpleNamespace

import pytest

from app.agents import sql_agent
from app.core import cs_dashboard, cs_order_details


QUESTIONS = [
    "주문번호 2026082224631661 CS 정보좀",
    "주문번호 139515930570616967 CS 정보 알려줘. (접수일 언제인지 ,출고송장 번호는 뭔지 등)",
    "네이버 주문정보 2026081423496661 CS 데이터 알려줘.",
    "2026081423496661 CS 정보 알려줘.",
]


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("mode", ["normal", "fast", "tool_loop"])
@pytest.mark.parametrize("question", QUESTIONS)
async def test_original_order_questions_keep_invoices_without_generation_or_cache(
    monkeypatch, streaming, mode, question,
):
    monkeypatch.setenv("BQ_FAST_ANSWER", "1" if mode == "fast" else "0")
    monkeypatch.setenv("BQ_TOOL_LOOP", "1" if mode == "tool_loop" else "0")
    calls = []

    def query(sql, **kwargs):
        calls.append(sql)
        # These two columns were absent from the SQL behind feedback #179.
        assert "outbound_invoice_nos" in sql and "return_invoice_nos" in sql
        assert "record_id" not in sql and "order_key" not in sql
        return [{
            "order_no": re.search(r"\d{12,}", question).group(),
            "channel": "스마트스토어" if "네이버" in question else "지그재그",
            "claim_type": "collection", "process_status": "completed",
            "received_at": date(2025, 12, 16), "completed_at": date(2025, 12, 22),
            "reason_normalized": "기타", "reason_details": ["송장 중복출력 이슈 건 상품회수"],
            "outbound_invoice_nos": ["540658923226"], "return_invoice_nos": ["844325444023"],
            "items": [{"product_name": "센텔라 앰플", "quantity": None}],
        }]

    def unexpected(*args, **kwargs):
        raise AssertionError("Exact CS order request escaped to cached/generated SQL or dashboard")

    monkeypatch.setattr(cs_order_details, "get_bigquery_client", lambda: SimpleNamespace(execute_query=query))
    monkeypatch.setattr(sql_agent, "generate_sql", unexpected)
    monkeypatch.setattr(sql_agent, "_cache_lookup", unexpected)
    monkeypatch.setattr(cs_dashboard, "_fetch_summary", unexpected)
    # The reporters belong to this sales brand group; CS source access is separate.
    kwargs = {"enabled_sources": ["국내CS"], "conversation_context": "", "brand_filter": "SK,CL,CBT"}
    if streaming:
        answer = "".join(sql_agent.run_sql_agent_stream(question, **kwargs))
    else:
        answer = await sql_agent.run_sql_agent(question, **kwargs)

    assert len(calls) == 1
    assert "540658923226" in answer and "844325444023" in answer
    assert "2025-12-16" in answer and "2025-12-22" in answer
    assert "센텔라 앰플" in answer
    assert "record_id" not in answer and "order_key" not in answer
    assert "조회 결과 원본" not in answer
    assert "http://34.64.99.254:8061/" in answer


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
async def test_additional_conditions_still_reach_sql_with_original_scope(monkeypatch, streaming):
    class SQLReached(Exception):
        pass

    def generate(state):
        assert state["enabled_sources"] == ["국내CS"]
        assert state["brand_filter"] == "SK,CBT"
        raise SQLReached

    def unexpected(*args, **kwargs):
        raise AssertionError("Order shortcut ignored an explicit brand filter")

    monkeypatch.delenv("BQ_TOOL_LOOP", raising=False)
    monkeypatch.setattr(sql_agent, "generate_sql", generate)
    monkeypatch.setattr(cs_order_details, "get_bigquery_client", unexpected)
    kwargs = {"enabled_sources": ["국내CS"], "brand_filter": "SK,CBT"}
    question = "SK 브랜드 상품만 " + QUESTIONS[0]
    with pytest.raises(SQLReached):
        if streaming:
            list(sql_agent.run_sql_agent_stream(question, **kwargs))
        else:
            await sql_agent.run_sql_agent(question, **kwargs)
