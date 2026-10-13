"""Order CS detail routing preserves explicit choices and never expands scope."""

import pytest

from app.agents import orchestrator as orch


QUESTIONS = (
    "주문번호 2026082224631661 CS 정보좀",
    "2026081423496661 CS 정보 알려줘.",
)


def _history(kind):
    if kind is None:
        return None
    previous_answer = {
        "bp": "BP 자료 원본 · 제품 Q&A와 CS 문서\n제품 Q&A에서는 주문 정보를 확인하지 못했습니다.",
        "sources_off": orch._sources_off_notice("bigquery", []) + "\n주문 조회가 연동되어 있지 않습니다.",
        "details": "**국내 CS 상세 기록 · 주문번호 2026082224631661**\n접수일: 2026-08-22",
    }[kind]
    return [
        {"role": "user", "content": QUESTIONS[0]},
        {"role": "assistant", "content": previous_answer},
    ]


@pytest.fixture
def wired_agent(monkeypatch):
    agent = orch.OrchestratorAgent()
    calls = []

    async def no_result(*args, **kwargs):
        return None

    async def no_wiki(*args, **kwargs):
        return ""

    def forbidden_llm(*args, **kwargs):
        calls.append(("llm",))
        raise AssertionError("deterministic CS order routing reached an LLM")

    async def forbidden_path(*args, **kwargs):
        calls.append(("unexpected",))
        raise AssertionError("order detail was clarified or sent to an unrelated route")

    async def sql(query, **kwargs):
        calls.append(("bigquery", query, kwargs.get("enabled_sources")))
        return "주문별 CS 상세 조회 결과"

    def sql_stream(query, *args, **kwargs):
        calls.append(("bigquery", query, kwargs.get("enabled_sources")))
        yield "주문별 CS 상세 조회 결과"

    async def bp(query, *args, **kwargs):
        calls.append(("cs", query))
        return {"source": "cs", "answer": "명시한 BP 자료 답변"}

    async def bp_stream(query, *args, **kwargs):
        calls.append(("cs", query))
        yield "명시한 BP 자료 답변"

    class NoMaintenance:
        active = False
        manual = False

    agent._handle_report = no_result
    agent._capture_bq_facts = no_result
    agent._verify_coherence = no_result
    agent._classify_with_llm = forbidden_path
    agent._ask_bq_clarification = forbidden_path
    agent._handle_direct = forbidden_path
    agent._handle_qdrant = forbidden_path
    agent._handle_cs = bp
    monkeypatch.setattr(orch, "run_sql_agent", sql)
    monkeypatch.setattr(orch, "get_llm_client", forbidden_llm)
    monkeypatch.setattr(orch, "get_flash_client", forbidden_llm)
    monkeypatch.setattr("app.agents.sql_agent.run_sql_agent_stream", sql_stream)
    monkeypatch.setattr("app.agents.cs_agent.run_stream", bp_stream)
    monkeypatch.setattr("app.core.safety.get_maintenance_manager", NoMaintenance)
    monkeypatch.setattr("app.core.notion_save.handle", lambda *args, **kwargs: None)
    monkeypatch.setattr("app.knowledge.wiki_search.search_with_pages", no_wiki)
    monkeypatch.setattr("app.agents.skill_memory.load_skill_context", lambda *args, **kwargs: "")
    return agent, calls


async def _ask(agent, query, streaming, sources=None, history=None):
    messages = _history(history)
    if messages:
        messages.append({"role": "user", "content": query})
    kwargs = {"enabled_sources": sources, "messages": messages, "can_view_fi": True}
    if streaming:
        events = [event async for event in agent.route_and_stream(query, **kwargs)]
        return {
            "source": [value for kind, value in events if kind == "source"][-1],
            "answer": "".join(value for kind, value in events if kind in ("chunk", "done")),
        }
    return await agent.route_and_execute(query, **kwargs)


@pytest.mark.parametrize("question", QUESTIONS + ("2026081423496661 CS",))
def test_incident_questions_are_confident_bigquery_and_need_no_sales_clarification(question):
    agent = orch.OrchestratorAgent()
    assert agent._keyword_classify_ex(question) == ("bigquery", True)
    assert not agent._bq_needs_clarification(question, "")


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("question", QUESTIONS)
@pytest.mark.parametrize("selection", ["default", "domestic", "all"])
@pytest.mark.parametrize("history", [None, "bp", "sources_off"])
async def test_order_details_reach_data_even_after_bp_or_disabled_source_history(
    wired_agent, streaming, question, selection, history,
):
    agent, calls = wired_agent
    sources = {"default": None, "domestic": ["국내CS"],
               "all": [entry["key"] for entry in agent.get_db_registry()]}[selection]
    result = await _ask(agent, question, streaming, sources, history)
    assert result["source"] == "bigquery"
    assert result["answer"] == "주문별 CS 상세 조회 결과"
    assert calls == [("bigquery", question, sources)]


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("question", [
    "국내 CS 주문번호 2026082224631661 정보와 링크 알려줘",
    "주문번호 2026082224631661 CS 정보와 링크 알려줘",
])
@pytest.mark.parametrize("sources", [None, ["국내CS", "해외CS"]])
async def test_order_details_with_links_reach_data_before_the_static_dashboard_catalog(
    wired_agent, streaming, question, sources,
):
    # Keep the real dashboard-link helper: it recognises these mixed requests too.
    from app.core.dashboard_links import answer_dashboard_link_query

    assert answer_dashboard_link_query(question)
    agent, calls = wired_agent
    result = await _ask(agent, question, streaming, sources)
    assert result["answer"] == "주문별 CS 상세 조회 결과"
    assert calls == [("bigquery", question, sources)]


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("sources", [[], ["매출"], ["해외CS"], ["매출", "BP"]])
@pytest.mark.parametrize("history", [None, "bp", "sources_off"])
async def test_missing_domestic_source_returns_only_the_specific_enable_instruction(
    wired_agent, streaming, sources, history,
):
    agent, calls = wired_agent
    result = await _ask(agent, QUESTIONS[1], streaming, sources, history)
    assert result["source"] == "direct"
    assert "국내 CS 소스가 꺼져" in result["answer"]
    assert "System Status" in result["answer"] and "@@국내CS" in result["answer"]
    assert "@@매출" not in result["answer"]
    assert "연동" not in result["answer"]
    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("source", ["BP", "CS"])
@pytest.mark.parametrize("browser_selection", [False, True])
async def test_explicit_bp_and_legacy_cs_choices_still_own_the_route(
    wired_agent, streaming, source, browser_selection,
):
    agent, calls = wired_agent
    question = QUESTIONS[1]
    result = await _ask(
        agent, question if browser_selection else f"@@{source} {question}", streaming,
        [source] if browser_selection else None,
    )
    assert result["source"] == ("cs:BP" if streaming and not browser_selection else "cs")
    assert result["answer"] == "명시한 BP 자료 답변"
    assert calls == [("cs", question)]


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
async def test_raw_domestic_prefix_preserves_the_exact_source_scope(wired_agent, streaming):
    agent, calls = wired_agent
    result = await _ask(agent, f"@@국내CS {QUESTIONS[0]}", streaming)
    assert result["source"] == ("bigquery:국내CS" if streaming else "bigquery")
    assert calls == [("bigquery", QUESTIONS[0], ["국내CS"])]


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("prefix,sources", [("@@매출", None), ("@@국내CS", [])])
async def test_raw_prefix_cannot_bypass_the_selected_domestic_scope(wired_agent, streaming, prefix, sources):
    agent, calls = wired_agent
    result = await _ask(agent, f"{prefix} {QUESTIONS[0]}", streaming, sources)
    assert "@@국내CS" in result["answer"]
    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("question", ["송장은?", "접수일 언제야?"])
@pytest.mark.parametrize("sources", [None, ["국내CS"]])
async def test_supported_detail_followups_keep_bigquery(wired_agent, streaming, question, sources):
    agent, calls = wired_agent
    result = await _ask(agent, question, streaming, sources, "details")
    assert result["source"] == "bigquery"
    assert calls == [("bigquery", question, sources)]


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("sources", [[], ["매출"], ["해외CS"]])
async def test_detail_followups_also_respect_current_source_selection(wired_agent, streaming, sources):
    agent, calls = wired_agent
    result = await _ask(agent, "송장은?", streaming, sources, "details")
    assert "@@국내CS" in result["answer"]
    assert calls == []


def test_order_detail_provenance_takes_priority_over_product_qa_words():
    context = "AI: **국내 CS 상세 기록 · 주문번호 2026082224631661**\n제품 Q&A가 아닌 접수 기록입니다."
    assert orch._previous_route(context) == "bigquery"


@pytest.mark.parametrize("question,route", [
    ("센텔라 앰플 사용법 알려줘", "cs"),
    ("해외 CS 환불 정책 알려줘", "notion"),
    ("8월 국내 CS 총 건수 알려줘", "bigquery"),
])
def test_product_policy_and_aggregate_queries_keep_their_routes(question, route):
    assert orch.OrchestratorAgent()._keyword_classify_ex(question)[0] == route
