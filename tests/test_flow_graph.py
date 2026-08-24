# -*- coding: utf-8 -*-
"""흐름 그래프가 **코드에서 생성**되는지 검증한다.

⛔ 손으로 그린 다이어그램은 반드시 낡는다. 이 프로젝트는 사본이 갈리는 사고를
   세 번 겪었다 (direct 프롬프트 두 벌 / @@ 목록 두 벌 / Continent1 값 두 벌).
"""
import pytest

from app.flow import graph, spec


def test_resolve_finds_module_level_function():
    fn = graph.resolve("app.agents.orchestrator._inherit_route_for_followup")
    assert callable(fn)


def test_resolve_walks_into_a_class():
    """⚠️ 라우터 판정은 **모듈 함수가 아니라 `OrchestratorAgent` 의 메서드**다.

    `resolve` 가 클래스 속성까지 따라가지 못하면 선언이 통째로 못 쓰게 된다.
    """
    fn = graph.resolve("app.agents.orchestrator.OrchestratorAgent._keyword_classify_ex")
    assert callable(fn)


def test_resolve_raises_on_missing_attribute():
    with pytest.raises(AttributeError):
        graph.resolve("app.agents.orchestrator._this_does_not_exist")


def test_every_declared_fn_resolves():
    """선언이 없는 함수를 가리키면 그림이 거짓말을 한다."""
    for node in spec.NODES:
        if node.fn:
            assert graph.resolve(node.fn) is not None, f"{node.id} → {node.fn}"


def test_bigquery_subgraph_is_extracted_from_langgraph():
    """⛔ 하위 노드를 손으로 적지 않는다 — LangGraph 객체에서 뽑는다.

    ⚠️ prefix 규칙은 **한 가지다**: 상위 노드 id 의 마지막 마디
       (`route.bigquery` → `bigquery`). `build()` 도 같은 규칙을 쓴다.
    """
    nodes, edges = graph.expand_langgraph(
        "app.agents.sql_agent.build_sql_agent_graph", prefix="bigquery")
    ids = {n.id for n in nodes}
    assert ids == {"bigquery.generate_sql", "bigquery.validate_sql",
                   "bigquery.execute_sql", "bigquery.format_answer"}


def test_conditional_edge_is_marked():
    """검증 실패 시 실행을 건너뛰는 분기 — 코드를 읽어야만 알던 사실이다."""
    _nodes, edges = graph.expand_langgraph(
        "app.agents.sql_agent.build_sql_agent_graph", prefix="bigquery")
    cond = {(e.src, e.dst) for e in edges if e.conditional}
    assert ("bigquery.validate_sql", "bigquery.execute_sql") in cond
    assert ("bigquery.validate_sql", "bigquery.format_answer") in cond


def test_start_and_end_sentinels_are_dropped():
    """__start__/__end__ 는 LangGraph 내부 표지라 캔버스에 그리지 않는다."""
    nodes, _edges = graph.expand_langgraph(
        "app.agents.sql_agent.build_sql_agent_graph", prefix="bigquery")
    assert not any("__start__" in n.id or "__end__" in n.id for n in nodes)


def test_build_returns_json_safe_dicts():
    out = graph.build()
    assert isinstance(out["nodes"], list) and isinstance(out["edges"], list)
    import json
    json.dumps(out)  # 직렬화 불가 타입이 섞이면 여기서 터진다


def test_build_covers_every_at_source_route():
    """⚠️ `@@` 소스를 새로 붙이면 캔버스에도 나타나야 한다."""
    from app.agents.orchestrator import OrchestratorAgent
    routes = {e["route"] for e in OrchestratorAgent._DB_REGISTRY}
    ids = {n["id"] for n in graph.build()["nodes"]}
    for r in routes:
        assert f"route.{r}" in ids, f"@@ 소스 라우트 {r} 가 캔버스에 없다"
