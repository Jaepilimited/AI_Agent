# 아키텍처 캔버스 v1 (흐름 그리기) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** admin 이 `/admin` 의 새 탭에서 요청 흐름(라우터 → 9경로 → 하위 단계)을 Dify 식 캔버스로 보고, 노드를 클릭해 그 노드의 파일·함수·현재 설정값을 확인한다.

**Architecture:** 흐름은 `app/flow/spec.py` 에 선언하고, `bigquery` 하위는 LangGraph 런타임 객체(`build_sql_agent_graph().get_graph()`)에서 **추출**한다 — 선언조차 하지 않으므로 어긋날 수 없다. 정적 검사가 선언↔코드 불일치를 매일 잡는다. 렌더는 이미 로드된 `vis-network` 를 재사용한다.

**Tech Stack:** Python 3.11 / FastAPI / LangGraph / vanilla JS + vis-network(이미 `chat.html:4` 에 로드됨) / pytest

## Global Constraints

- **그래프는 생성된다, 그려지지 않는다.** 노드·엣지를 손으로 중복 기재하지 않는다. LangGraph 로 된 하위 그래프는 반드시 런타임 추출.
- **판정 로직은 `app/core/static_checks.py` 한 곳에만.** pytest 와 자가 점검이 **같은 함수**를 부른다 (서버에는 pytest 도 `tests/` 도 없다).
- **admin 판정은 서버에서 DB 조회로.** `Depends(_require_admin)` 재사용. JWT·프론트 값 신뢰 금지.
- **CSS/JS 변경 시 `chat.html` 의 `?v=` 증가 + `CLAUDE.md` 의 캐시 버전 줄도 함께 갱신.** 안 하면 `test_cache_version_doc_matches_reality` 가 실패한다. 현재: `style.css?v=167`, `chat.js?v=256`.
- **정적 검사 함수는 `(ok: bool, detail: str)` 을 돌려주고 부작용이 없어야 한다.**
- 새 파이썬 패키지를 추가하지 않는다 (배포 시 휠 재업로드가 필요해진다).

---

### Task 1: 흐름 선언 + LangGraph 추출기

**Files:**
- Create: `app/flow/__init__.py`
- Create: `app/flow/spec.py`
- Create: `app/flow/graph.py`
- Test: `tests/test_flow_graph.py`

**Interfaces:**
- Consumes: `app.agents.sql_agent.build_sql_agent_graph`, `app.agents.orchestrator.OrchestratorAgent._DB_REGISTRY`
- Produces:
  - `spec.Node(id, label, fn=None, subgraph=None, knobs=(), group="main")` — frozen dataclass
  - `spec.Edge(src, dst, label="", conditional=False)` — frozen dataclass
  - `spec.NODES: tuple[Node, ...]`, `spec.EDGES: tuple[Edge, ...]`
  - `graph.resolve(dotted: str) -> object` — `"app.agents.orchestrator._keyword_classify_ex"` 를 실제 객체로. 없으면 `AttributeError`
  - `graph.expand_langgraph(ref: str, prefix: str) -> tuple[list[Node], list[Edge]]`
  - `graph.build() -> dict` — `{"nodes": [...], "edges": [...]}` (JSON 직렬화 가능한 dict 목록)

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
# tests/test_flow_graph.py
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
```

- [ ] **Step 2: 실패를 확인한다**

Run: `python -m pytest tests/test_flow_graph.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.flow'`

- [ ] **Step 3: `app/flow/__init__.py` 를 만든다**

```python
# -*- coding: utf-8 -*-
"""요청 실행 흐름의 선언과 조립.

⚠️ `knowledge_map/`(파일 의존 그래프, 1,832 노드)과 **다른 층위**다.
   이건 요청이 지나는 실행 흐름(약 25~30 노드)이다. 섞지 않는다.
"""
```

- [ ] **Step 4: `app/flow/spec.py` 를 만든다**

```python
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
```

위 `fn` 문자열은 전부 2026-08-24 실측으로 확인한 실제 이름이다
(`answer_check.verify` 는 112행, `OrchestratorAgent._keyword_classify_ex` 는 1897행).
그래도 어긋나면 `test_every_declared_fn_resolves` 가 어느 노드인지 짚어 준다.

- [ ] **Step 5: `app/flow/graph.py` 를 만든다**

```python
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
```

- [ ] **Step 6: 테스트를 통과시킨다**

Run: `python -m pytest tests/test_flow_graph.py -q`
Expected: PASS (8 passed)

실패하면 대개 `spec.py` 의 `fn` 문자열이 실제 이름과 다른 것이다. `resolve` 가 올린 `AttributeError` 메시지에 어느 노드인지 나온다.

- [ ] **Step 7: 커밋**

```bash
git add app/flow tests/test_flow_graph.py
git commit -m "feat(flow): 요청 흐름 선언 + LangGraph 하위 그래프 런타임 추출"
```

---

### Task 2: 선언 ↔ 코드 일치 정적 검사

**Files:**
- Modify: `app/core/static_checks.py` (`ALL` 에 등록)
- Test: `tests/test_no_silent_failures.py` (기존 파라미터 테스트가 자동으로 잡는다 + 전용 테스트 추가)

**Interfaces:**
- Consumes: `app.flow.graph.build`, `app.flow.spec.NODES`, `app.flow.graph.resolve`
- Produces: `static_checks.flow_spec_matches_code() -> Tuple[bool, str]`, `ALL` 에 `("static_flow_spec", flow_spec_matches_code, "흐름 선언 ↔ 코드 일치")`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
# tests/test_no_silent_failures.py 끝에 추가
def test_flow_spec_check_catches_dangling_function_reference():
    """선언이 없는 함수를 가리키면 그림이 거짓말을 한다 — 반드시 잡혀야 한다."""
    from app.flow import spec
    from app.core import static_checks as SC

    bad = spec.Node("ghost", "유령", fn="app.agents.orchestrator._nope_not_here")
    original = spec.NODES
    spec.NODES = original + (bad,)
    try:
        ok, detail = SC.flow_spec_matches_code()
        assert ok is False
        assert "ghost" in detail
    finally:
        spec.NODES = original


def test_flow_spec_check_catches_missing_at_source_route():
    """`@@` 소스를 새로 붙였는데 캔버스에 노드가 없으면 잡아야 한다."""
    from app.agents.orchestrator import OrchestratorAgent
    from app.core import static_checks as SC

    original = OrchestratorAgent._DB_REGISTRY
    OrchestratorAgent._DB_REGISTRY = original + [
        {"key": "테스트소스", "aliases": [], "route": "nowhere",
         "group": "x", "icon": "chart", "label": "x", "desc": "x"}
    ]
    try:
        ok, detail = SC.flow_spec_matches_code()
        assert ok is False
        assert "nowhere" in detail
    finally:
        OrchestratorAgent._DB_REGISTRY = original
```

- [ ] **Step 2: 실패를 확인한다**

Run: `python -m pytest tests/test_no_silent_failures.py -q -k flow_spec`
Expected: FAIL — `AttributeError: module 'app.core.static_checks' has no attribute 'flow_spec_matches_code'`

- [ ] **Step 3: 검사를 구현한다**

`app/core/static_checks.py` 의 `ALL = [` 바로 위에 넣는다:

```python
# ── 9) 흐름 선언 ↔ 코드 일치 ────────────────────────────────────────────────

def flow_spec_matches_code() -> Tuple[bool, str]:
    """캔버스가 그리는 흐름이 실제 코드와 같은가.

    ⛔ 이 검사가 죽으면 **기능 전체가 무의미하다.** 그림이 코드와 갈리는 순간
       캔버스는 이 프로젝트의 네 번째 "사본이 갈린 사고"가 된다
       (direct 프롬프트 두 벌 / @@ 목록 두 벌 / Continent1 값 두 벌).

    두 방향을 본다:
      · 선언 → 코드 : 노드가 가리키는 함수가 실제로 있는가
      · 코드 → 선언 : `@@` 레지스트리의 라우트가 캔버스에 노드로 있는가
    """
    try:
        from app.agents.orchestrator import OrchestratorAgent
        from app.flow import graph, spec
    except Exception as e:
        return False, f"흐름 모듈 로드 실패: {str(e)[:120]}"

    problems: List[str] = []
    for node in spec.NODES:
        for dotted in (node.fn, node.subgraph):
            if not dotted:
                continue
            try:
                graph.resolve(dotted)
            except Exception as e:
                problems.append(f"{node.id}→{dotted} ({type(e).__name__})")

    try:
        node_ids = {n["id"] for n in graph.build()["nodes"]}
    except Exception as e:
        return False, f"그래프 조립 실패: {str(e)[:120]}"

    for route in sorted({e["route"] for e in OrchestratorAgent._DB_REGISTRY}):
        if f"route.{route}" not in node_ids:
            problems.append(f"@@ 라우트 '{route}' 노드 없음")

    if problems:
        return False, ("흐름 선언이 코드와 어긋난다 "
                       f"{len(problems)}건: " + ", ".join(problems[:4]))
    return True, f"노드 {len(node_ids)}개 · 선언과 코드 일치"
```

그리고 `ALL` 목록에 한 줄 추가한다:

```python
    ("static_flow_spec", flow_spec_matches_code, "흐름 선언 ↔ 코드 일치"),
```

- [ ] **Step 4: 테스트를 통과시킨다**

Run: `python -m pytest tests/test_no_silent_failures.py -q`
Expected: PASS — 기존 파라미터 테스트(`test_static_check[static_flow_spec]`)도 함께 통과해야 한다.

- [ ] **Step 5: 자가 점검에도 자동 등록됐는지 확인한다**

Run:
```bash
python -c "from app.core.self_check import CHECKS; print(len(CHECKS), any(c.id=='static_flow_spec' for c in CHECKS))"
```
Expected: `30 True` (기존 29 + 1). `self_check` 는 `SC.ALL` 을 순회하므로 별도 등록이 필요 없다.

- [ ] **Step 6: 커밋**

```bash
git add app/core/static_checks.py tests/test_no_silent_failures.py
git commit -m "feat(flow): 흐름 선언과 코드가 어긋나면 잡는 정적 검사"
```

---

### Task 3: Admin API `GET /api/admin/flow`

**Files:**
- Modify: `app/api/admin_api.py` (파일 끝에 추가)
- Test: `tests/test_flow_api.py`

**Interfaces:**
- Consumes: `app.flow.graph.build`, `app.api.admin_api._require_admin`
- Produces: `GET /api/admin/flow` → `{"nodes": [...], "edges": [...], "generated_at": "<ISO8601>"}`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
# tests/test_flow_api.py
# -*- coding: utf-8 -*-
"""흐름 API 는 admin 전용이고, 그래프를 그대로 돌려준다."""
import inspect


def test_endpoint_is_registered():
    from app.api.admin_api import admin_router
    paths = {r.path for r in admin_router.routes}
    assert "/api/admin/flow" in paths


def test_endpoint_requires_admin():
    """⛔ 권한 판정은 서버에서 한다 — 프론트가 탭을 숨기는 것에 기대지 않는다."""
    from app.api import admin_api
    sig = inspect.signature(admin_api.get_flow)
    deps = [p.default for p in sig.parameters.values()]
    assert any(getattr(d, "dependency", None) is admin_api._require_admin
               for d in deps), "_require_admin 의존성이 없다"
```

- [ ] **Step 2: 실패를 확인한다**

Run: `python -m pytest tests/test_flow_api.py -q`
Expected: FAIL — `AttributeError: module 'app.api.admin_api' has no attribute 'get_flow'`

- [ ] **Step 3: 엔드포인트를 구현한다**

`app/api/admin_api.py` 파일 끝에 추가:

```python
@admin_router.get("/flow")
async def get_flow(_: User = Depends(_require_admin)) -> dict:
    """요청 실행 흐름 그래프 (아키텍처 캔버스용).

    ⚠️ 그래프는 **코드에서 생성**된다 — 손으로 그린 사본이 아니다.
       어긋남은 자가 점검 `static_flow_spec` 이 매일 잡는다.
    """
    from app.flow.graph import build

    out = await asyncio.to_thread(build)
    out["generated_at"] = datetime.now().isoformat(timespec="seconds")
    return out
```

`asyncio` 와 `datetime` 은 이미 이 파일 상단에서 import 되어 있다 (3·4행).

- [ ] **Step 4: 테스트를 통과시킨다**

Run: `python -m pytest tests/test_flow_api.py -q`
Expected: PASS (2 passed)

- [ ] **Step 5: 로컬에서 실제 응답을 확인한다**

Run:
```bash
python -c "
import asyncio, json
from app.flow.graph import build
g = build()
print('nodes', len(g['nodes']), 'edges', len(g['edges']))
print(json.dumps(g['nodes'][:3], ensure_ascii=False, indent=1))
"
```
Expected: `nodes` 가 20 이상(상위 16 + bigquery 하위 4), `edges` 가 30 이상.

- [ ] **Step 6: 커밋**

```bash
git add app/api/admin_api.py tests/test_flow_api.py
git commit -m "feat(flow): GET /api/admin/flow — 흐름 그래프 API (admin 전용)"
```

---

### Task 4: 캔버스 탭 (프론트)

**Files:**
- Modify: `app/frontend/chat.html` — 탭 버튼(265~272행 블록), 탭 콘텐츠 div, 캐시 버전 `chat.js?v=256` → `257`
- Modify: `app/frontend/chat.js` — 탭 클릭 핸들러(약 4602행), `loadFlowCanvas()` 추가
- Modify: `CLAUDE.md` — 캐시 버전 줄 (`chat.js=256` → `257`)
- Test: `tests/test_flow_frontend.py`

**Interfaces:**
- Consumes: `GET /api/admin/flow`, 전역 `vis` (이미 `chat.html:4` 에서 로드됨)
- Produces: 탭 `data-tab="flow"`, 컨테이너 `id="flow-canvas"`, 상세 패널 `id="flow-detail"`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
# tests/test_flow_frontend.py
# -*- coding: utf-8 -*-
"""프론트/서버 짝이 어긋나면 탭이 **에러 없이** 빈 화면이 된다.

`@@` 목록이 두 벌이라 조용히 어긋났던 사고와 같은 부류라 같은 방식으로 막는다.
"""
import io
import os

from app.core import static_checks as SC


def _read(rel):
    with io.open(os.path.join(SC.ROOT, rel), encoding="utf-8") as fh:
        return fh.read()


def test_flow_tab_button_exists():
    assert 'data-tab="flow"' in _read("app/frontend/chat.html")


def test_flow_tab_content_container_exists():
    html = _read("app/frontend/chat.html")
    assert 'id="tab-flow"' in html
    assert 'id="flow-canvas"' in html


def test_flow_tab_is_wired_in_js():
    """버튼만 있고 로더가 없으면 눌러도 아무 일이 없다."""
    js = _read("app/frontend/chat.js")
    assert 'tab.dataset.tab === "flow"' in js
    assert "function loadFlowCanvas" in js


def test_js_uses_hierarchical_layout_not_physics():
    """⚠️ 흐름도는 좌→우 계층 배치다. 위키 그래프의 물리엔진을 그대로 쓰면
    노드가 뭉쳐서 흐름으로 읽히지 않는다."""
    js = _read("app/frontend/chat.js")
    assert "hierarchical" in js and "'LR'" in js
```

- [ ] **Step 2: 실패를 확인한다**

Run: `python -m pytest tests/test_flow_frontend.py -q`
Expected: FAIL (4 failed)

- [ ] **Step 3: `chat.html` 에 탭 버튼을 추가한다**

266~272행의 버튼 목록 마지막(`붐따 처리함` 다음)에 한 줄 추가:

```html
      <button class="admin-tab" data-tab="flow">아키텍처</button>
```

- [ ] **Step 4: `chat.html` 에 탭 콘텐츠를 추가한다**

다른 `admin-tab-content` div 들과 같은 위치(예: `id="tab-selfcheck"` 블록 다음)에 추가:

```html
    <div class="admin-tab-content" id="tab-flow">
      <div style="display:flex;gap:12px;height:70vh">
        <div id="flow-canvas" style="flex:1;min-width:0;border:1px solid var(--border);border-radius:8px"></div>
        <div id="flow-detail" style="width:320px;overflow:auto;padding:12px;border:1px solid var(--border);border-radius:8px;color:var(--text)">
          노드를 클릭하세요.
        </div>
      </div>
    </div>
```

⚠️ 색은 반드시 `style.css` 상단 `html.dark`/`html.light` 에 **실제로 있는** 토큰만 쓴다
(`--bg` `--bg-surface` `--bg-elevated` `--bg-input` `--bg-hover` `--border` `--border-strong`
`--text` `--text-secondary` `--text-muted` `--shadow-card`).
없는 이름은 에러가 아니라 **폴백**이라 아무도 못 잡는다.

- [ ] **Step 5: `chat.html` 의 캐시 버전을 올린다**

`chat.js?v=256` → `chat.js?v=257`

- [ ] **Step 6: `CLAUDE.md` 의 캐시 버전 줄을 함께 고친다**

```
- 현재: style.css?v=167, chat.js?v=257 (2026-08-24 기준 — 올릴 때 이 줄도 같이 갱신할 것)
```

⚠️ 안 고치면 `test_cache_version_doc_matches_reality` 가 실패한다.
그리고 `python scripts/sync_agents_md.py` 를 돌려 `AGENTS.md` 도 맞춘다.

- [ ] **Step 7: `chat.js` 에 로더를 추가한다**

탭 클릭 핸들러(약 4602행)의 `if (tab.dataset.tab === "feedback") ...` 다음 줄에 추가:

```javascript
      if (tab.dataset.tab === "flow") loadFlowCanvas();
```

그리고 같은 파일 안(다른 `load*` 함수들 근처)에 함수를 추가:

```javascript
  // ── 아키텍처 캔버스 ──
  // ⛔ 그래프는 서버가 **코드에서 생성**해 준다. 여기서 노드를 손으로 그리지 마라 —
  //    그리는 순간 코드와 갈리고, 그건 이 프로젝트가 세 번 당한 사고다.
  var _flowNetwork = null;
  function loadFlowCanvas() {
    var detail = document.getElementById("flow-detail");
    // ⚠️ 이 앱의 admin fetch 는 **세션 쿠키** 방식이다. 헤더를 직접 붙이지 마라 —
    //    `authHeaders()` 같은 헬퍼는 이 파일에 없다 (2026-08-24 확인).
    //    다른 admin 로더도 그냥 `fetch("/api/admin/self-check")` 를 쓴다 (chat.js:4745).
    fetch("/api/admin/flow")
      .then(function(r) { return r.ok ? r.json() : Promise.reject(r.status); })
      .then(function(g) {
        var byId = {};
        var nodes = g.nodes.map(function(n) {
          byId[n.id] = n;
          var color = n.group === "route" ? "#e89200"
                    : n.group === "sub" ? "#5b8def"
                    : n.group === "io" ? "#7a7a7a" : "#3aa675";
          return {
            id: n.id, label: n.label, shape: "box",
            color: { background: color, border: color },
            font: { color: "#fff", size: 12 },
          };
        });
        var edges = g.edges.map(function(e) {
          return {
            from: e.src, to: e.dst, label: e.label || undefined,
            arrows: "to",
            dashes: !!e.conditional,
            color: { color: e.conditional ? "#c76a00" : "rgba(128,128,128,0.5)" },
            font: { size: 9, color: "var(--text-muted)", strokeWidth: 0 },
          };
        });
        var container = document.getElementById("flow-canvas");
        container.innerHTML = "";
        _flowNetwork = new vis.Network(container,
          { nodes: new vis.DataSet(nodes), edges: new vis.DataSet(edges) },
          {
            layout: { hierarchical: { direction: "LR", sortMethod: "directed",
                                      levelSeparation: 190, nodeSpacing: 90 } },
            physics: false,
            interaction: { hover: true, tooltipDelay: 200 },
          });
        _flowNetwork.on("click", function(params) {
          if (!params.nodes || !params.nodes.length) return;
          var n = byId[params.nodes[0]];
          if (!n) return;
          var knobs = (n.knobs || []).map(function(k) {
            return "<li><code>" + escapeHtml(k) + "</code></li>";
          }).join("");
          detail.innerHTML =
            "<h3 style='margin:0 0 8px'>" + escapeHtml(n.label) + "</h3>" +
            "<div style='color:var(--text-muted);font-size:12px'>" + escapeHtml(n.id) + "</div>" +
            (n.fn ? "<p style='margin:10px 0 4px'><b>실행 지점</b><br><code>"
                    + escapeHtml(n.fn) + "</code></p>" : "") +
            (knobs ? "<p style='margin:10px 0 4px'><b>설정값</b></p><ul>" + knobs + "</ul>" : "") +
            (n.has_subgraph ? "<p style='color:var(--text-secondary)'>하위 그래프 있음 "
                              + "(LangGraph 에서 자동 추출)</p>" : "");
        });
        detail.innerHTML = "노드를 클릭하세요. (생성 " + escapeHtml(g.generated_at || "") + ")";
      })
      .catch(function(e) {
        detail.innerHTML = "<span style='color:#e05555'>흐름을 불러오지 못했습니다: "
                           + escapeHtml(String(e)) + "</span>";
      });
  }
```

⚠️ `escapeHtml()` 은 `chat.js:5794` 에 이미 있다 (2026-08-24 확인). 새로 만들지 마라.
발견(finding) 문자열을 그대로 innerHTML 에 넣으면 HTML 주입이 된다 — 반드시 거쳐라.

- [ ] **Step 8: 테스트를 통과시킨다**

Run: `python -m pytest tests/test_flow_frontend.py tests/test_no_silent_failures.py -q`
Expected: PASS (캐시 버전 검사 포함)

- [ ] **Step 9: 실제 화면을 확인한다**

```bash
pm2 restart skin1004-dev
```
브라우저에서 `http://localhost:3001` → 로그인 → Admin → `아키텍처` 탭.
확인할 것:
- 좌→우로 흐름이 읽히는가 (뭉쳐 있으면 `hierarchical` 이 안 먹은 것)
- `bigquery` 노드 옆에 `generate_sql → validate_sql → execute_sql → format_answer` 가 보이는가
- `validate_sql` 에서 나가는 두 엣지가 **점선**인가 (조건부)
- 노드를 클릭하면 우측에 파일·함수·설정값이 뜨는가
- 라이트 모드에서도 글자가 보이는가 (테마 토큰 확인)

- [ ] **Step 10: 커밋**

```bash
git add app/frontend/chat.html app/frontend/chat.js CLAUDE.md AGENTS.md tests/test_flow_frontend.py
git commit -m "feat(flow): Admin 아키텍처 캔버스 탭 (vis-network 계층 배치)"
```

---

### Task 5: 배포 및 확인

**Files:** 없음 (배포만)

**Interfaces:**
- Consumes: Task 1~4 의 산출물 전부

- [ ] **Step 1: 전체 테스트를 돌린다**

Run:
```bash
python -m pytest tests/test_flow_graph.py tests/test_flow_api.py tests/test_flow_frontend.py tests/test_no_silent_failures.py -q
```
Expected: 전부 PASS

⚠️ `tests/test_report_rules.py::test_insight_uses_claude_planner_uses_gemini` 는
이 개발 환경에 `anthropic` 모듈이 없어 실패한다 — **기존부터 그랬고 이 작업과 무관하다.**

- [ ] **Step 2: 배포 목록에 새 패키지가 드는지 먼저 확인한다**

⚠️ `deploy_new_server.py` 의 `EXCLUDE_DIRS` 는 **디렉토리 이름**으로 거른다.
`app/knowledge_map/` 이 이름 충돌로 통째로 빠진 사고가 있었다 (2026-08-05).
`app/flow/` 가 목록에 드는지 반드시 확인한다:

```bash
./sshenv/Scripts/python -c "
import importlib.util, os, sys
os.chdir(r'C:\Users\DB_PC\Desktop\python_bcj\AI_Agent'); sys.path.insert(0,'.')
s = importlib.util.spec_from_file_location('dep','scripts/deploy_new_server.py')
m = importlib.util.module_from_spec(s); s.loader.exec_module(m)
files = {str(p).replace(os.sep,'/') for p in m.collect()}
for t in ['app/flow/spec.py','app/flow/graph.py','app/flow/__init__.py']:
    print(('OK   ' if any(f.endswith(t) for f in files) else '누락 ')+t)
"
```
Expected: 셋 다 `OK`. 하나라도 `누락` 이면 `EXCLUDE_DIRS`/`EXCLUDE_PATHS` 를 확인한다.

- [ ] **Step 3: 프로덕션에 배포한다**

```bash
CRAVER_SSH_PW='<노션 "AI Craver" 페이지 참조>' ./sshenv/Scripts/python scripts/deploy_new_server.py was
```
Expected: `서비스: active` · `/health HTTP 200` · `기동 오류: 0건`

- [ ] **Step 4: 서버에서 정적 검사를 확인한다**

배포 후 서버에서 새 검사가 통과하는지 본다 (`/tmp/_sc.py` 로 올려 실행):

```python
import sys; sys.path.insert(0, '.')
from app.core import static_checks as SC
for cid, fn, _ in SC.ALL:
    ok, detail = fn()
    print(('OK  ' if ok else 'FAIL'), cid, '|', detail[:70])
```
Expected: `OK static_flow_spec | 노드 N개 · 선언과 코드 일치` 포함, 전부 OK

- [ ] **Step 5: 커밋 및 마무리**

```bash
git add -A app/flow app/api/admin_api.py app/core/static_checks.py \
        app/frontend CLAUDE.md AGENTS.md tests/
git commit -m "feat(flow): 아키텍처 캔버스 v1 — 흐름 그리기"
```

---

## v1 이후 (이 계획의 범위 밖)

설계 문서 `docs/superpowers/specs/2026-08-24-architecture-canvas-design.md` 의 2·3단계.
**화면이 나온 뒤에 다시 판단한다** — 무엇이 필요한지가 그때 훨씬 구체적으로 보인다.

- 2단계: `request_id` 관통 + 단계별 span → 노드에 호출 수·p50/p95·실패율 오버레이
- 3단계: 노드 편집 (🟢 정적검사 / 🟡 골든셋 관문, DB 오버레이, 되돌리기)

⛔ **노드 재배선과 빈 캔버스 신규 워크플로우는 만들지 않는다.** 전자는 런타임을 그래프
해석기로 바꾸는 재설계고, 후자는 Dify 가 제일 잘하는 일이다.
