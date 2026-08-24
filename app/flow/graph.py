# -*- coding: utf-8 -*-
"""선언 + LangGraph 추출 → 캔버스용 그래프.

⛔ LangGraph 하위 그래프는 **선언하지 않는다.** `build_sql_agent_graph().get_graph()`
   가 노드·엣지·조건부 여부를 그대로 준다 — 선언이 곧 실행이라 어긋날 수 없다.
"""
from __future__ import annotations

import importlib
from dataclasses import dataclass
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


@dataclass(frozen=True)
class SubGraph:
    """LangGraph 에서 뽑은 하위 그래프 — 노드·엣지 말고 진입/이탈점도 담는다.

    ⛔ 진입/이탈점을 하드코딩하면 (`"generate_sql"`, `"format_answer"`) 이 그래프
       추출 기능 전체가 지키려던 것을 그대로 재현한다 — LangGraph 를 다시 배선하는
       순간 조용히 낡는다. `__start__`/`__end__` 에 붙은 엣지가 이미 알고 있으므로
       버리기 전에 읽어서 여기 담는다.
    """
    nodes: Tuple[Node, ...]
    edges: Tuple[Edge, ...]
    entries: Tuple[str, ...]   # __start__ 가 가리키던 실제 진입 노드 (prefix 포함)
    exits: Tuple[str, ...]     # __end__ 로 이어지던 실제 이탈 노드 (prefix 포함)


def expand_langgraph(ref: str, prefix: str) -> SubGraph:
    """LangGraph 빌더에서 노드·엣지·진입/이탈점을 뽑는다.

    `__start__`/`__end__` 는 내부 표지라 캔버스 노드로 그리지 않는다 — 하지만
    버리기 전에 그 표지가 물고 있던 진짜 노드를 진입/이탈점으로 기록해 둔다.
    상위 노드(`route.bigquery` 등)를 이 하위 그래프에 이어 붙이려면 어디로
    들어가고 어디서 나오는지가 있어야 한다 (`build()` 가 그 배선을 한다).
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
    entries = tuple(sorted({
        f"{prefix}.{e.target}" for e in g.edges if e.source == "__start__"
    }))
    exits = tuple(sorted({
        f"{prefix}.{e.source}" for e in g.edges if e.target == "__end__"
    }))
    return SubGraph(nodes=tuple(nodes), edges=tuple(edges), entries=entries, exits=exits)


def _as_dict(n: Node) -> Dict[str, Any]:
    # `unreachable` 은 **빈 문자열이 정상**이고, 값이 있으면 그 자체가 이유 문구다.
    # 화면에서 흐릿하게·점선으로 그려 "엣지를 빠뜨린 것"과 구분한다.
    return {"id": n.id, "label": n.label, "fn": n.fn, "group": n.group,
            "knobs": list(n.knobs), "has_subgraph": bool(n.subgraph),
            "unreachable": n.unreachable}


def build() -> Dict[str, Any]:
    """캔버스가 그릴 전체 그래프 (JSON 직렬화 가능).

    ⚠️ 하위 그래프가 있는 노드(`route.bigquery` 등)는 **그 자리에 이어 붙인다** —
       하위 노드만 떼어 그리면 본 흐름과 끊긴 섬이 된다 (2026-08-24 실측: vis-network
       가 560px 떨어진 별도 컴포넌트로 배치해 SQL 검증 실패 시 실행을 건너뛰는
       분기가 화면에서 통째로 안 보였다). 배선은 세 단계다:
         1. 상위 노드 → 하위 그래프의 진입 노드
         2. 하위 그래프의 이탈 노드 → 상위 노드가 원래 가리키던 곳
         3. 상위 노드의 원래 직행 엣지는 하위 그래프가 대신하므로 뺀다
    """
    nodes: List[Dict[str, Any]] = [_as_dict(n) for n in NODES]

    subgraphs: Dict[str, SubGraph] = {
        n.id: expand_langgraph(n.subgraph, prefix=n.id.split(".")[-1])
        for n in NODES if n.subgraph
    }

    # 하위 그래프가 대신할 상위 노드의 직행 엣지는 뺀다 (2단계에서 이탈점이 잇는다)
    edges: List[Dict[str, Any]] = [
        {"src": e.src, "dst": e.dst, "label": e.label, "conditional": e.conditional}
        for e in EDGES if e.src not in subgraphs
    ]

    for parent_id, sub in subgraphs.items():
        nodes.extend({**_as_dict(s), "parent": parent_id} for s in sub.nodes)
        edges.extend(
            {"src": e.src, "dst": e.dst, "label": e.label,
             "conditional": e.conditional, "parent": parent_id}
            for e in sub.edges
        )
        downstream = [e.dst for e in EDGES if e.src == parent_id]
        edges.extend(
            {"src": parent_id, "dst": entry, "label": "", "conditional": False,
             "parent": parent_id}
            for entry in sub.entries
        )
        edges.extend(
            {"src": exit_id, "dst": dst, "label": "", "conditional": False,
             "parent": parent_id}
            for exit_id in sub.exits for dst in downstream
        )

    return {"nodes": nodes, "edges": edges}
