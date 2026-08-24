# -*- coding: utf-8 -*-
"""선언 + LangGraph 추출 → 캔버스용 그래프.

⛔ LangGraph 하위 그래프는 **선언하지 않는다.** `build_sql_agent_graph().get_graph()`
   가 노드·엣지·조건부 여부를 그대로 준다 — 선언이 곧 실행이라 어긋날 수 없다.
"""
from __future__ import annotations

import importlib
from typing import Any, Dict, List, Tuple

from app.flow.spec import EDGES, NODES, Edge, Node

_SENTINELS = ("__start__", "__end__")


def resolve(dotted: str) -> Any:
    """"패키지.모듈.속성[.속성]" → 실제 객체. 없으면 AttributeError."""
    parts = dotted.split(".")
    for split in range(len(parts) - 1, 0, -1):
        try:
            mod = importlib.import_module(".".join(parts[:split]))
        except ImportError:
            continue
        obj = mod
        for attr in parts[split:]:
            obj = getattr(obj, attr)  # 없으면 AttributeError — 그대로 올린다
        return obj
    raise AttributeError(f"resolve 실패: {dotted}")


def expand_langgraph(ref: str, prefix: str) -> Tuple[List[Node], List[Edge]]:
    """LangGraph 빌더에서 노드·엣지를 뽑는다.

    `__start__`/`__end__` 는 내부 표지라 버린다 — 캔버스에는 상위 노드가 그 자리다.
    """
    compiled = resolve(ref)()
    g = compiled.get_graph()
    nodes = [
        Node(f"{prefix}.{name}", name, group="sub")
        for name in g.nodes if name not in _SENTINELS
    ]
    edges = [
        Edge(f"{prefix}.{e.source}", f"{prefix}.{e.target}",
             conditional=bool(getattr(e, "conditional", False)))
        for e in g.edges
        if e.source not in _SENTINELS and e.target not in _SENTINELS
    ]
    return nodes, edges


def _as_dict(n: Node) -> Dict[str, Any]:
    return {"id": n.id, "label": n.label, "fn": n.fn, "group": n.group,
            "knobs": list(n.knobs), "has_subgraph": bool(n.subgraph)}


def build() -> Dict[str, Any]:
    """캔버스가 그릴 전체 그래프 (JSON 직렬화 가능)."""
    nodes: List[Dict[str, Any]] = [_as_dict(n) for n in NODES]
    edges: List[Dict[str, Any]] = [
        {"src": e.src, "dst": e.dst, "label": e.label, "conditional": e.conditional}
        for e in EDGES
    ]
    for n in NODES:
        if not n.subgraph:
            continue
        sub_nodes, sub_edges = expand_langgraph(n.subgraph, prefix=n.id.split(".")[-1])
        nodes.extend({**_as_dict(s), "parent": n.id} for s in sub_nodes)
        edges.extend(
            {"src": e.src, "dst": e.dst, "label": e.label,
             "conditional": e.conditional, "parent": n.id}
            for e in sub_edges
        )
    return {"nodes": nodes, "edges": edges}
