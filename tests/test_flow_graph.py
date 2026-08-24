# -*- coding: utf-8 -*-
"""흐름 그래프가 **코드에서 생성**되는지 검증한다.

⛔ 손으로 그린 다이어그램은 반드시 낡는다. 이 프로젝트는 사본이 갈리는 사고를
   세 번 겪었다 (direct 프롬프트 두 벌 / @@ 목록 두 벌 / Continent1 값 두 벌).
"""
import collections

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
    sub = graph.expand_langgraph(
        "app.agents.sql_agent.build_sql_agent_graph", prefix="bigquery")
    ids = {n.id for n in sub.nodes}
    assert ids == {"bigquery.generate_sql", "bigquery.validate_sql",
                   "bigquery.execute_sql", "bigquery.format_answer"}


def test_conditional_edge_is_marked():
    """검증 실패 시 실행을 건너뛰는 분기 — 코드를 읽어야만 알던 사실이다."""
    sub = graph.expand_langgraph(
        "app.agents.sql_agent.build_sql_agent_graph", prefix="bigquery")
    cond = {(e.src, e.dst) for e in sub.edges if e.conditional}
    assert ("bigquery.validate_sql", "bigquery.execute_sql") in cond
    assert ("bigquery.validate_sql", "bigquery.format_answer") in cond


def test_start_and_end_sentinels_are_dropped():
    """__start__/__end__ 는 LangGraph 내부 표지라 캔버스에 그리지 않는다."""
    sub = graph.expand_langgraph(
        "app.agents.sql_agent.build_sql_agent_graph", prefix="bigquery")
    assert not any("__start__" in n.id or "__end__" in n.id for n in sub.nodes)


def test_entry_and_exit_are_derived_not_hardcoded():
    """⛔ 진입/이탈점을 `"generate_sql"`/`"format_answer"` 로 손으로 적으면, 이
    기능이 막으려던 사고(사본이 갈리는 것)가 그대로 재현된다. `__start__`/`__end__`
    에 붙은 엣지에서 실제로 읽어 왔는지를 확인한다 — 오늘의 LangGraph 배선과
    우연히 같은 값이 아니라, 배선이 바뀌어도 따라가는지가 핵심이다."""
    sub = graph.expand_langgraph(
        "app.agents.sql_agent.build_sql_agent_graph", prefix="bigquery")
    assert sub.entries == ("bigquery.generate_sql",)
    assert sub.exits == ("bigquery.format_answer",)


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


def test_build_wires_subgraph_into_main_flow():
    """⛔ 실제 사고: bigquery.* 4개가 서로만 연결된 섬이었다 — 브라우저에서
    vis-network 가 560px 떨어진 별도 컴포넌트로 그렸다. `route.bigquery` 가 진입
    노드로, 이탈 노드가 원래 목적지(`answer_check`)로 이어져야 한다. 대체된
    상위 노드의 직행 엣지(`route.bigquery` → `answer_check`)는 더는 없어야 한다
    — 남아 있으면 SQL 검증을 건너뛴 것처럼 보이는 거짓 화살표가 하나 더 생긴다."""
    out = graph.build()
    pairs = {(e["src"], e["dst"]) for e in out["edges"]}
    assert ("route.bigquery", "bigquery.generate_sql") in pairs
    assert ("bigquery.format_answer", "answer_check") in pairs
    assert ("route.bigquery", "answer_check") not in pairs


def test_build_has_no_orphan_nodes():
    """모든 노드가 최소 한 엣지에 등장해야 한다."""
    out = graph.build()
    node_ids = {n["id"] for n in out["nodes"]}
    touched = set()
    for e in out["edges"]:
        touched.add(e["src"])
        touched.add(e["dst"])
    assert node_ids <= touched, f"엣지에 전혀 등장하지 않는 노드: {node_ids - touched}"


def test_build_has_no_disconnected_islands():
    """⚠️ 위 '엣지가 하나라도 있다'만으로는 부족하다 — 실측해 보니 고쳐지기 전
    코드에서도 bigquery.* 4개는 전부 '엣지가 있는' 노드였다 (서로서로만 연결돼
    있었을 뿐). 진짜 사고는 **본 흐름과 끊긴 별도 컴포넌트**였다는 것이라, `input`
    에서 엣지를 무방향으로 따라가 전체 노드에 닿는지를 봐야 이 결함이 잡힌다."""
    out = graph.build()
    node_ids = {n["id"] for n in out["nodes"]}
    adjacency = collections.defaultdict(set)
    for e in out["edges"]:
        adjacency[e["src"]].add(e["dst"])
        adjacency[e["dst"]].add(e["src"])
    reached = set()
    stack = ["input"]
    while stack:
        cur = stack.pop()
        if cur in reached:
            continue
        reached.add(cur)
        stack.extend(adjacency[cur] - reached)
    assert reached == node_ids, f"input 에서 도달할 수 없는 노드: {node_ids - reached}"
