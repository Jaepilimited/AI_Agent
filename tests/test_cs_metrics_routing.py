"""CS operational records use BigQuery while product QA and CS documents share BP."""

from pathlib import Path

import pytest

from app.agents import orchestrator as orch
from app.agents.orchestrator import OrchestratorAgent


@pytest.mark.parametrize("key,aliases", [
    ("국내CS", ("국내CS", "국내 CS", "domestic CS", "domestic_cs")),
    ("해외CS", ("해외CS", "해외 CS", "overseas CS", "overseas_cs", "글로벌 CS", "글로벌CS")),
])
def test_metrics_sources_have_distinct_names_and_clean_aliases(key, aliases):
    for alias in aliases:
        entry, query = OrchestratorAgent.parse_db_prefix(f"@@{alias} 처리 건수 알려줘")
        assert entry["key"] == key
        assert entry["route"] == "bigquery"
        assert entry["group"] == "고객지원"
        assert entry["icon"] == "headset"
        assert query == "처리 건수 알려줘"


def test_metrics_source_group_is_visible_without_a_second_source_list():
    from app.core.static_checks import at_source_parity

    ok, detail = at_source_parity()
    assert ok, detail
    js = Path("app/frontend/chat.js").read_text(encoding="utf-8")
    assert 'id: "customer_support"' in js
    assert '"고객지원": "customer_support"' in js
    for key in ("국내CS", "해외CS"):
        assert f'"{key}":' in js


@pytest.mark.parametrize("query", [
    "8월 국내 CS 처리 건수 알려줘",
    "국내 CS 보상 유형별 건수 알려줘",
    "해외 CS 환불 사유별 건수 알려줘",
    "해외 CS 환불액 합계 알려줘",
    "이번 달 글로벌 자사몰 CS 인입 건수 알려줘",
])
def test_operational_cs_questions_are_confident_bigquery(query):
    assert OrchestratorAgent()._keyword_classify_ex(query) == ("bigquery", True)


@pytest.mark.parametrize("query,route", [
    ("국내 CS 운영 매뉴얼 찾아줘", "notion"),
    ("해외 CS 환불 정책 알려줘", "notion"),
    ("센텔라 앰플 사용법 알려줘", "cs"),
])
def test_document_and_product_consultations_keep_their_existing_routes(query, route):
    assert OrchestratorAgent()._keyword_classify_ex(query)[0] == route


@pytest.fixture
def wired_agent(monkeypatch):
    """Exercise the real routing entrypoints with every external operation replaced."""
    agent = OrchestratorAgent()
    calls = []

    async def no_result(*args, **kwargs):
        return None

    async def no_wiki(*args, **kwargs):
        return ""

    async def no_llm_classification(*args, **kwargs):
        raise AssertionError("explicit or operational CS request reached LLM classification")

    async def bigquery(query, *args, **kwargs):
        calls.append(("bigquery", query, kwargs.get("enabled_sources")))
        return {"source": "bigquery", "answer": "metrics answer"}

    def bigquery_stream(query, *args, **kwargs):
        calls.append(("bigquery", query, kwargs.get("enabled_sources")))
        yield "metrics answer"

    async def notion(query, *args, **kwargs):
        calls.append(("notion", query, kwargs.get("team_key")))
        return {"source": "notion", "answer": "document answer"}

    async def product_qa(query, *args, **kwargs):
        calls.append(("cs", query, None))
        return {"source": "cs", "answer": "product answer"}

    async def product_qa_stream(query, *args, **kwargs):
        calls.append(("cs", query, None))
        yield "product answer"

    async def direct(*args, **kwargs):
        return {"source": "direct", "answer": "direct answer"}

    class NoMaintenance:
        active = False
        manual = False

    agent._handle_report = no_result
    agent._verify_coherence = no_result
    agent._classify_with_llm = no_llm_classification
    agent._handle_bigquery = bigquery
    agent._handle_qdrant = notion
    agent._handle_cs = product_qa
    agent._handle_direct = direct
    agent._build_direct_system_prompt = lambda: "test system prompt"
    agent._needs_web_search = lambda query: False
    monkeypatch.setattr("app.core.notion_save.handle", lambda *args, **kwargs: None)
    monkeypatch.setattr("app.core.safety.get_maintenance_manager", NoMaintenance)
    monkeypatch.setattr("app.knowledge.wiki_search.search_with_pages", no_wiki)
    monkeypatch.setattr("app.agents.skill_memory.load_skill_context", lambda *args, **kwargs: "")
    monkeypatch.setattr("app.agents.sql_agent.run_sql_agent_stream", bigquery_stream)
    monkeypatch.setattr("app.agents.cs_agent.run_stream", product_qa_stream)
    monkeypatch.setattr(orch, "get_llm_client", lambda *args, **kwargs: object())
    monkeypatch.setattr(orch, "get_flash_client", lambda: object())
    monkeypatch.setattr(orch, "_stream_direct_with_fallback", lambda *args, **kwargs: iter(("direct answer",)))
    return agent, calls


async def _ask(agent, query, streaming, sources=None, messages=None):
    kwargs = {"enabled_sources": sources, "messages": messages, "can_view_fi": True}
    if streaming:
        events = [event async for event in agent.route_and_stream(query, **kwargs)]
        return "".join(data for kind, data in events if kind in ("chunk", "done"))
    return (await agent.route_and_execute(query, **kwargs))["answer"]


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("sources", [None, ["국내CS"], ["해외CS"], ["국내CS", "해외CS"]])
async def test_browser_selection_preserves_bq_scope_in_both_entrypoints(wired_agent, streaming, sources):
    agent, calls = wired_agent
    question = "8월 국내 CS 처리 건수 알려줘"
    answer = await _ask(agent, question, streaming, sources)
    assert answer == "metrics answer"
    assert calls == [("bigquery", question, sources)]


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("question", ["국내 CS 현황", "해외 CS 현황"])
async def test_dashboard_summary_reaches_data_without_sales_clarification(wired_agent, monkeypatch, streaming, question):
    agent, calls = wired_agent

    async def sql(query, **kwargs):
        calls.append(("bigquery", query, kwargs.get("enabled_sources")))
        return "metrics answer"

    async def no_capture(*args, **kwargs):
        return None

    monkeypatch.setattr(agent, "_handle_bigquery", OrchestratorAgent._handle_bigquery.__get__(agent))
    monkeypatch.setattr(agent, "_capture_bq_facts", no_capture)
    monkeypatch.setattr(orch, "run_sql_agent", sql)
    result = await _ask(agent, question, streaming)
    assert result == "metrics answer"
    assert calls == [("bigquery", question, None)]


def test_sales_questions_still_clarify_without_period_or_channel():
    assert OrchestratorAgent()._bq_needs_clarification("매출 현황", "")


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
async def test_an_explicit_all_sources_list_preserves_metrics_routing(wired_agent, streaming):
    agent, calls = wired_agent
    sources = [entry["key"] for entry in agent.get_db_registry()]
    answer = await _ask(agent, "해외 CS 환불액 합계 알려줘", streaming, sources)
    assert answer == "metrics answer"
    assert calls[0][0] == "bigquery"
    assert calls[0][2] == sources


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("prefix,keys", [
    ("@@국내 CS", ["국내CS"]),
    ("@@글로벌 CS", ["해외CS"]),
    ("@@국내CS @@해외CS", ["국내CS", "해외CS"]),
    ("@@해외CS @@국내CS", ["국내CS", "해외CS"]),
])
async def test_raw_prefixes_clean_the_question_and_scope_both_modes(wired_agent, streaming, prefix, keys):
    agent, calls = wired_agent
    answer = await _ask(agent, f"{prefix} 8월 건수 알려줘", streaming)
    assert "metrics answer" in answer
    assert calls == [("bigquery", "8월 건수 알려줘", keys)]
    if len(keys) == 2:
        assert "### 국내·해외 CS" in answer
        assert answer.count("metrics answer") == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("keys", [["국내CS", "매출"], ["국내CS", "해외CS", "매출"]])
async def test_other_bigquery_prefix_combinations_keep_their_existing_fanout(wired_agent, streaming, keys):
    agent, calls = wired_agent
    prefix = " ".join(f"@@{key}" for key in keys)
    answer = await _ask(agent, f"{prefix} 8월 건수 알려줘", streaming)
    assert calls == [("bigquery", "8월 건수 알려줘", keys)] * len(keys)
    assert answer.count("metrics answer") == len(keys)
    assert "### 국내·해외 CS" not in answer


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
async def test_empty_selection_blocks_cs_metrics_queries(wired_agent, streaming):
    agent, calls = wired_agent
    answer = await _ask(agent, "8월 국내 CS 처리 건수 알려줘", streaming, [])
    assert not calls
    assert "데이터 조회를 실행하지 않았습니다" in answer
    assert answer.endswith("direct answer")


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("browser_selection", [False, True])
@pytest.mark.parametrize("key,route,scope", [("CS", "cs", None), ("BP", "cs", None)])
async def test_cs_document_alias_and_bp_share_the_product_agent(
    wired_agent, streaming, browser_selection, key, route, scope,
):
    agent, calls = wired_agent
    question = "센텔라 앰플 사용법 알려줘"
    answer = await _ask(
        agent, question if browser_selection else f"@@{key} {question}", streaming,
        [key] if browser_selection else None,
    )
    assert answer
    assert calls == [(route, question, scope)]


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("browser_selection", [False, True])
async def test_bp_and_old_cs_alias_execute_only_once(wired_agent, streaming, browser_selection):
    agent, calls = wired_agent
    question = "센텔라 앰플 사용법 알려줘"
    await _ask(
        agent, question if browser_selection else f"@@CS @@BP {question}", streaming,
        ["CS", "BP"] if browser_selection else None,
    )
    assert calls == [("cs", question, None)]


def _metrics_history():
    return [
        {"role": "user", "content": "국내 CS 처리 건수 알려줘"},
        {"role": "assistant", "content": (
            "국내 CS 데이터 조회 결과입니다.\n"
            "<details><summary>조회 SQL</summary>\n```sql\n"
            "SELECT COUNT(*) FROM `skin1004-319714.cs_dashboard.domestic_cs_records`\n"
            "```\n</details>"
        )},
        {"role": "user", "content": "지난달은?"},
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
async def test_cs_metrics_followup_uses_the_executed_table_before_the_cs_word(wired_agent, streaming):
    agent, calls = wired_agent
    await _ask(agent, "지난달은?", streaming, messages=_metrics_history())
    assert calls == [("bigquery", "지난달은?", None)]


def test_a_later_document_answer_overrides_an_older_cs_metrics_sql_anchor():
    messages = _metrics_history()[:2] + [
        {"role": "user", "content": "반품 정책 찾아줘"},
        {"role": "assistant", "content": "Notion 사내 문서 검색 결과: 반품 정책입니다."},
        {"role": "user", "content": "교환은?"},
    ]
    context = orch._build_conversation_context(messages)
    assert orch._previous_route(context) == "notion"
