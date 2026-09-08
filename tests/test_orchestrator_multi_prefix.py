"""Regression coverage for @@ multi-source dispatch parity."""
import pytest

from app.agents import orchestrator as orch_module
from app.agents.orchestrator import OrchestratorAgent


async def _no_report(*args, **kwargs):
    return None


def _prefix_entries(*routes):
    return [
        {"key": route, "route": route, "label": route.upper()}
        for route in routes
    ]


async def _collect(stream):
    return [event async for event in stream]


@pytest.mark.asyncio
async def test_streaming_single_source_uses_new_handler_route(monkeypatch):
    """A newly registered handler must work in @@ streaming without a third table."""
    agent = OrchestratorAgent.__new__(OrchestratorAgent)
    calls = []

    async def synthetic(*args, **kwargs):
        calls.append("synthetic")
        return {"source": "synthetic", "answer": "synthetic handler"}

    async def direct(*args, **kwargs):
        raise AssertionError("registered handler incorrectly fell back to direct")

    agent._handle_synthetic = synthetic
    agent._handle_direct = direct
    agent._handle_report = _no_report
    agent.parse_db_prefix = lambda query: (_prefix_entries("synthetic"), "lookup")
    monkeypatch.setattr(orch_module, "HANDLER_ROUTES",
                        orch_module.HANDLER_ROUTES + ("synthetic",))

    events = await _collect(agent.route_and_stream("@@synthetic lookup"))

    assert calls == ["synthetic"]
    assert any(kind == "chunk" and "synthetic handler" in data for kind, data in events)


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
async def test_multi_prefix_uses_gws_handler_in_both_execution_modes(monkeypatch, streaming):
    """@@ GWS fan-out must not silently become direct in the streaming branch."""
    agent = OrchestratorAgent.__new__(OrchestratorAgent)
    calls = []

    async def gws(*args, **kwargs):
        calls.append("gws")
        return {"source": "gws", "answer": "gws answer"}

    async def direct(*args, **kwargs):
        calls.append("direct")
        return {"source": "direct", "answer": "direct answer"}

    agent._handle_gws = gws
    agent._handle_direct = direct
    agent._handle_report = _no_report
    agent.parse_db_prefix = lambda query: (_prefix_entries("gws", "unknown"), "lookup")

    if streaming:
        events = await _collect(agent.route_and_stream("@@gws @@unknown lookup"))
        answer = "".join(data for kind, data in events if kind == "chunk")
    else:
        result = await agent.route_and_execute("@@gws @@unknown lookup")
        answer = result["answer"]

    assert calls.count("gws") == 1
    assert "gws answer" in answer


@pytest.mark.asyncio
async def test_enabled_sources_downgrades_classified_notion_to_direct():
    """A route outside enabled sources must execute direct rather than its classifier target."""
    agent = OrchestratorAgent.__new__(OrchestratorAgent)

    async def direct(*args, **kwargs):
        return {"source": "direct", "answer": "direct answer"}

    async def notion(*args, **kwargs):
        raise AssertionError("disabled notion route executed")

    agent._handle_direct = direct
    agent._handle_qdrant = notion
    agent._handle_report = _no_report
    agent.parse_db_prefix = lambda query: (None, query)
    agent._keyword_classify_ex = lambda query: ("notion", True)

    result = await agent.route_and_execute("lookup", enabled_sources=[])

    assert result["source"] == "direct"
    assert result["answer"] == "direct answer"
