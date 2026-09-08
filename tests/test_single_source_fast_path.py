"""Single-source selections must bypass work needed only for source discovery."""

import pytest

from app.agents import orchestrator as orch_module
from app.agents.orchestrator import OrchestratorAgent


async def _no_report(*args, **kwargs):
    return None


async def _collect(stream):
    return [event async for event in stream]


class _NoMaintenance:
    active = False
    manual = False


def _prepare_agent(monkeypatch):
    agent = OrchestratorAgent()
    agent.parse_db_prefix = lambda query: (None, query)
    agent._handle_report = _no_report
    monkeypatch.setattr(orch_module, "get_flash_client", lambda: object())
    monkeypatch.setattr(
        "app.core.safety.get_maintenance_manager", lambda: _NoMaintenance()
    )
    monkeypatch.setattr(
        "app.agents.skill_memory.load_skill_context", lambda *args, **kwargs: ""
    )
    return agent


@pytest.mark.asyncio
async def test_stream_single_bigquery_source_skips_discovery_and_uses_only_that_source(
    monkeypatch,
):
    """Removing the raw @@ token in the browser must not re-enable discovery work."""
    agent = _prepare_agent(monkeypatch)
    calls = []

    def keyword_classify(query):
        calls.append("keyword_classifier")
        return "direct", False

    async def llm_classify(*args, **kwargs):
        calls.append("llm_classifier")
        return "bigquery"

    async def wiki_search(*args, **kwargs):
        calls.append("wiki")
        return ""

    def run_bigquery(*args, **kwargs):
        calls.append(("bigquery", tuple(kwargs.get("enabled_sources") or ())))
        yield "single-source answer"

    agent._keyword_classify_ex = keyword_classify
    agent._classify_with_llm = llm_classify
    monkeypatch.setattr(
        "app.knowledge.wiki_search.search_with_pages", wiki_search
    )
    monkeypatch.setattr(
        "app.agents.sql_agent.run_sql_agent_stream", run_bigquery
    )

    events = await _collect(
        agent.route_and_stream("lookup", enabled_sources=["매출"])
    )

    assert calls == [("bigquery", ("매출",))]
    assert ("source", "bigquery") in events
    assert any(
        kind == "chunk" and "single-source answer" in data
        for kind, data in events
    )


@pytest.mark.asyncio
async def test_nonstream_single_bigquery_source_skips_route_classification(monkeypatch):
    agent = _prepare_agent(monkeypatch)
    calls = []

    def keyword_classify(query):
        calls.append("keyword_classifier")
        return "direct", False

    async def llm_classify(*args, **kwargs):
        calls.append("llm_classifier")
        return "bigquery"

    async def run_bigquery(*args, **kwargs):
        calls.append(("bigquery", tuple(kwargs.get("enabled_sources") or ())))
        return {"source": "bigquery", "sentinel": "single-source answer"}

    agent._keyword_classify_ex = keyword_classify
    agent._classify_with_llm = llm_classify
    agent._handle_bigquery = run_bigquery

    result = await agent.route_and_execute("lookup", enabled_sources=["매출"])

    assert calls == [("bigquery", ("매출",))]
    assert result == {"source": "bigquery", "sentinel": "single-source answer"}


@pytest.mark.asyncio
async def test_stream_single_notion_source_keeps_its_team_scope_and_skips_wiki(
    monkeypatch,
):
    agent = _prepare_agent(monkeypatch)
    calls = []

    async def wiki_search(*args, **kwargs):
        calls.append("wiki")
        return ""

    async def run_notion(*args, **kwargs):
        calls.append(("notion", kwargs.get("team_key")))
        return {"source": "notion", "answer": "scoped notion answer"}

    agent._keyword_classify_ex = lambda query: (_ for _ in ()).throw(
        AssertionError("single source reached keyword classification")
    )
    agent._handle_qdrant = run_notion
    monkeypatch.setattr(
        "app.knowledge.wiki_search.search_with_pages", wiki_search
    )

    events = await _collect(
        agent.route_and_stream("lookup", enabled_sources=["DB"])
    )

    assert calls == [("notion", "DB")]
    assert ("source", "notion") in events


@pytest.mark.asyncio
async def test_stream_multiple_sources_keep_existing_discovery_flow(monkeypatch):
    """Two or more selected sources must retain the existing routing architecture."""
    agent = _prepare_agent(monkeypatch)
    calls = []

    def keyword_classify(query):
        calls.append("keyword_classifier")
        return "bigquery", True

    async def wiki_search(*args, **kwargs):
        calls.append("wiki")
        return ""

    def run_bigquery(*args, **kwargs):
        calls.append(("bigquery", tuple(kwargs.get("enabled_sources") or ())))
        yield "multi-selection answer"

    agent._keyword_classify_ex = keyword_classify
    monkeypatch.setattr(
        "app.knowledge.wiki_search.search_with_pages", wiki_search
    )
    monkeypatch.setattr(
        "app.agents.sql_agent.run_sql_agent_stream", run_bigquery
    )

    await _collect(
        agent.route_and_stream(
            "lookup", enabled_sources=["매출", "광고"]
        )
    )

    assert calls == [
        "keyword_classifier",
        "wiki",
        ("bigquery", ("매출", "광고")),
    ]
