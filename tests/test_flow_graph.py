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


def _reachable_ids(out):
    """도달 불가로 **명시한** 노드를 뺀 나머지 — 연결성 검사의 대상."""
    unreachable = {n["id"] for n in out["nodes"] if n.get("unreachable")}
    return {n["id"] for n in out["nodes"]} - unreachable


def test_build_has_no_orphan_nodes():
    """모든 노드가 최소 한 엣지에 등장해야 한다 (도달 불가 표시분 제외)."""
    out = graph.build()
    touched = set()
    for e in out["edges"]:
        touched.add(e["src"])
        touched.add(e["dst"])
    node_ids = _reachable_ids(out)
    assert node_ids <= touched, f"엣지에 전혀 등장하지 않는 노드: {node_ids - touched}"


# ── 2026-08-24 리뷰: 그린 엣지 37개 중 13개가 코드가 하지 않는 일을 주장했다 ──
# 아래 네 테스트가 그 13개를 각각 못질한다. 캔버스의 명제가
# *그래프는 생성된다, 그려지지 않는다* 이므로, 이 테스트들이 그 명제 자체다.


def test_router_out_edges_equal_the_classifier_route_universe():
    """⛔ 라우터가 낼 수 없는 경로로 화살표를 그리면 **없는 길을 알려준다.**

    실제 사고: 두 라우터 노드가 라우트 9종 전부로 부챗살을 그렸는데
    `_keyword_classify_ex` 와 `_classify_with_llm` 은 여섯만 낸다 — 거짓 화살표 6개.
    `report`·`model_rights` 는 심지어 **분류기보다 위**에서 가로채므로 방향까지
    거꾸로였다. CLAUDE.md 는 이 화면이 "왜 보고서가 안 만들어지지"에 답하길
    요구하는데, 그림대로 라우터를 뒤지면 거기엔 아무것도 없다.
    """
    from app.agents.orchestrator import ROUTER_ROUTES

    out = graph.build()
    for router in ("router.keyword", "router.llm"):
        drawn = {e["dst"][len("route."):] for e in out["edges"]
                 if e["src"] == router and e["dst"].startswith("route.")}
        assert drawn == set(ROUTER_ROUTES), (
            f"{router} 의 나가는 엣지가 분류기와 다르다: "
            f"더 그림 {sorted(drawn - set(ROUTER_ROUTES))} / "
            f"빠짐 {sorted(set(ROUTER_ROUTES) - drawn)}")


def test_keyword_classifier_returns_only_router_routes():
    """⚠️ 위 테스트는 `ROUTER_ROUTES` 상수가 맞다는 전제 위에 있다. 상수가 코드와
    갈리면 둘이 사이좋게 틀린다 — 그래서 **함수 본문에서 직접** 읽어 대조한다.
    `_keyword_classify_ex` 가 돌려주는 `("<route>", bool)` 리터럴이 전부다.
    """
    import inspect
    import re

    from app.agents.orchestrator import ROUTER_ROUTES, OrchestratorAgent

    src = inspect.getsource(OrchestratorAgent._keyword_classify_ex)
    returned = set(re.findall(r'return\s*\(\s*"([a-z_]+)"\s*,', src))
    assert returned, "반환 리터럴을 하나도 못 읽었다 — 정규식이 낡았다"
    assert returned <= set(ROUTER_ROUTES), (
        f"분류기가 ROUTER_ROUTES 밖의 값을 낸다: {sorted(returned - set(ROUTER_ROUTES))}")


def test_pre_router_intercepts_sit_upstream_of_the_router():
    """보고서·초상권은 분류기가 **돌기도 전에** 가로챈다 (orchestrator.py 의
    route_and_execute/route_and_stream 모두 분류기 호출보다 위에 있다).
    캔버스가 이걸 라우터 하류로 그리면 실제 제어 흐름의 정반대다."""
    out = graph.build()
    pairs = {(e["src"], e["dst"]) for e in out["edges"]}
    assert ("intercept.model_rights", "route.model_rights") in pairs
    assert ("intercept.report", "route.report") in pairs
    # 관문 → … → 라우터 순서여야 한다 (라우터가 관문보다 위면 안 된다)
    assert ("intercept.report", "source_pin") in pairs
    for router in ("router.keyword", "router.llm"):
        assert (router, "route.report") not in pairs
        assert (router, "route.model_rights") not in pairs


def test_answer_check_hangs_off_bigquery_only():
    """⛔ 안전장치가 덮지 않는 경로를 덮는다고 주장하는 것이 이 기능이 낼 수 있는
    최악의 사고다. `answer_check` 의 호출부는 앱 전체에 **하나뿐**이다
    (`sql_agent.format_answer` 안, 2026-08-24 전수 확인) — 그런데 캔버스는 7개
    경로가 전부 수치검증을 거친다고 그렸다.

    ⚠️ `multi` 도 `_multi_prepare` 로 sql_agent 를 부르지만, 그 답은 Flash 합성을
       한 번 더 거쳐 나가므로 **최종 답변이 검증된 것이 아니다** — 그리지 않는다.
    """
    out = graph.build()
    incoming = {e["src"] for e in out["edges"] if e["dst"] == "answer_check"}
    assert incoming == {"bigquery.format_answer"}, (
        f"answer_check 로 들어오는 엣지가 하나가 아니다: {sorted(incoming)}")


def test_unreachable_node_is_marked_and_has_no_edges():
    """`team` 은 `_handle_team` 과 디스패치 배선이 살아 있는데 어떤 진입점도
    만들지 않는다. 화살표를 그리면 거짓말이고, 노드를 지우면 죽은 배선이 조용히
    남는다 — 이유를 달아 '도달 불가' 로 표시하는 것이 세 번째 선택지다.
    표시와 그림이 어긋나지 않는지(엣지 0개)를 여기서 못질한다."""
    out = graph.build()
    marked = [n for n in out["nodes"] if n.get("unreachable")]
    assert [n["id"] for n in marked] == ["route.team"]
    for n in marked:
        assert n["unreachable"].strip(), "도달 불가면 이유를 적어야 한다"
        touching = [e for e in out["edges"]
                    if e["src"] == n["id"] or e["dst"] == n["id"]]
        assert not touching, f"{n['id']} 는 도달 불가인데 엣지가 있다: {touching}"


def test_every_route_the_code_can_produce_has_a_node():
    """⛔ 예전 역방향 검사는 `_DB_REGISTRY` 만 봐서 `direct`·`team`·`multi` 처럼
    `@@` 로 고를 수 없는 라우터 전용 경로를 **아예 못 봤다.** 열 번째 경로를
    추가하면 캔버스 어디에도 없는데 검사는 계속 '일치' 라고 답했을 것이다."""
    from app.agents.orchestrator import (HANDLER_ROUTES, ROUTER_ROUTES,
                                         OrchestratorAgent)

    every = (set(ROUTER_ROUTES) | set(HANDLER_ROUTES)
             | {e["route"] for e in OrchestratorAgent._DB_REGISTRY})
    ids = {n["id"] for n in graph.build()["nodes"]}
    for r in sorted(every):
        assert f"route.{r}" in ids, f"코드가 만들 수 있는 경로 {r} 가 캔버스에 없다"


def test_build_has_no_disconnected_islands():
    """⚠️ 위 '엣지가 하나라도 있다'만으로는 부족하다 — 실측해 보니 고쳐지기 전
    코드에서도 bigquery.* 4개는 전부 '엣지가 있는' 노드였다 (서로서로만 연결돼
    있었을 뿐). 진짜 사고는 **본 흐름과 끊긴 별도 컴포넌트**였다는 것이라, `input`
    에서 엣지를 무방향으로 따라가 전체 노드에 닿는지를 봐야 이 결함이 잡힌다."""
    out = graph.build()
    node_ids = _reachable_ids(out)
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
