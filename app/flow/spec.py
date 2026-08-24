# -*- coding: utf-8 -*-
"""흐름 선언 — 노드는 **실제 실행 함수·레지스트리를 가리킨다**.

⛔ 여기에 하위 단계를 손으로 적지 마라. LangGraph 로 된 것은 `subgraph=` 로
   런타임에서 추출한다 (`app/flow/graph.py`).
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Node:
    id: str
    label: str
    fn: str | None = None          # "모듈.속성" — 실제 실행 지점
    subgraph: str | None = None    # "모듈.빌더" (LangGraph) — 런타임 추출
    knobs: tuple[str, ...] = ()    # 3단계에서 편집 대상이 될 후보
    group: str = "main"


@dataclass(frozen=True)
class Edge:
    src: str
    dst: str
    label: str = ""
    conditional: bool = False


_ORCH = "app.agents.orchestrator"

NODES: tuple[Node, ...] = (
    Node("input", "USER INPUT", group="io"),
    Node("at_parse", "@@ 소스 파싱", fn=f"{_ORCH}.OrchestratorAgent.get_db_registry",
         knobs=("orchestrator._DB_REGISTRY",)),
    Node("followup", "후속 경로 상속", fn=f"{_ORCH}._inherit_route_for_followup",
         knobs=("orchestrator._METRIC_NOUNS",)),
    Node("router.keyword", "라우터 · 키워드", fn=f"{_ORCH}.OrchestratorAgent._keyword_classify_ex",
         knobs=("orchestrator._DATA_KEYWORDS", "orchestrator._STRONG_DATA",
                "orchestrator._GUARDED")),
    Node("router.llm", "라우터 · LLM 재판정", fn=f"{_ORCH}.OrchestratorAgent._classify_with_llm",
         knobs=("prompts/query_analyzer.txt",)),

    Node("route.direct", "direct", group="route"),
    Node("route.bigquery", "bigquery", group="route",
         subgraph="app.agents.sql_agent.build_sql_agent_graph"),
    Node("route.notion", "notion", group="route"),
    Node("route.cs", "cs", group="route"),
    Node("route.gws", "gws", group="route"),
    Node("route.team", "team", group="route"),
    Node("route.model_rights", "model_rights", group="route"),
    Node("route.report", "report", group="route"),
    Node("route.multi", "multi", group="route"),

    Node("answer_check", "답변 수치검증", fn="app.core.answer_check.verify"),
    Node("response", "응답", group="io"),
)

_ROUTE_IDS = tuple(n.id for n in NODES if n.group == "route")

EDGES: tuple[Edge, ...] = (
    Edge("input", "at_parse"),
    Edge("at_parse", "followup"),
    Edge("followup", "router.keyword"),
    Edge("router.keyword", "router.llm", label="확신 없음", conditional=True),
    *tuple(Edge("router.keyword", r, label="확신", conditional=True) for r in _ROUTE_IDS),
    *tuple(Edge("router.llm", r, conditional=True) for r in _ROUTE_IDS),
    *tuple(Edge(r, "answer_check") for r in _ROUTE_IDS),
    Edge("answer_check", "response"),
)
