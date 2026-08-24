# -*- coding: utf-8 -*-
"""흐름 선언 — 노드는 **실제 실행 함수·레지스트리를 가리킨다**.

⛔ 여기에 하위 단계를 손으로 적지 마라. LangGraph 로 된 것은 `subgraph=` 로
   런타임에서 추출한다 (`app/flow/graph.py`).

⛔ **없는 화살표를 그리지 마라. 빠진 것보다 나쁘다.**
   2026-08-24 리뷰에서 그린 엣지 37개 중 13개가 코드가 하지 않는 일을 주장하고
   있었다. 이 기능의 명제가 *그래프는 생성된다, 그려지지 않는다* 인데, 정작 이
   선언이 손으로 그린 ASCII 다이어그램(설계문서 76~88행)을 그대로 옮겨 적어
   그 명제를 어겼다. 두 부류였다:

     · 라우터가 9개 경로를 낸다고 그렸다 — 분류기는 6개만 낸다.
       게다가 `report`·`model_rights` 는 **분류기보다 먼저** 가로채는 관문이라
       화살표 방향이 거꾸로였다. admin 이 "왜 보고서가 안 만들어지지"를 이 그림으로
       쫓으면 라우터를 뒤지게 되고, 거기엔 아무것도 없다 (진짜 관문은 위쪽이다).
     · 모든 경로가 답변 수치검증을 거친다고 그렸다 — `answer_check` 의 호출부는
       앱 전체에 **하나뿐**이다 (`sql_agent.format_answer`). 안전장치가 실제로
       덮지 않는 경로를 덮는다고 주장하는 것이 이 기능이 낼 수 있는 최악의 사고다.

   그래서 여기 적는 엣지는 전부 코드 위치를 댈 수 있어야 한다. 확인 못 한 경로는
   **그리지 않는다** — 불완전한 그림은 사람이 코드를 더 읽게 만들 뿐이지만,
   틀린 그림은 코드를 안 읽게 만든다.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Node:
    id: str
    label: str
    fn: str | None = None          # "모듈.속성" — 실제 실행 지점
    subgraph: str | None = None    # "모듈.빌더" (LangGraph) — 런타임 추출
    knobs: tuple[str, ...] = ()    # 3단계에서 편집 대상이 될 후보
    group: str = "main"
    unreachable: str = ""          # 비어 있지 않으면 "도달 불가" + 그 이유


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

    # ── 분류기보다 **먼저** 도는 관문 ──
    # 두 경로 모두 `route_and_execute`(비스트리밍) 와 `route_and_stream`(스트리밍)
    # 에서 분류기 호출보다 위에 있고, 걸리면 그 자리에서 return 한다.
    Node("intercept.model_rights", "초상권 가로채기",
         fn="app.core.model_rights.model_rights_intent",
         knobs=("@@초상권 지정", "사진 첨부 시 얼굴 인식")),
    Node("intercept.report", "보고서 가로채기",
         fn="app.reports.registry.wants_report",
         knobs=("registry._REPORT_META", "@@보고서 지정")),

    # ── 소스가 이미 경로를 정한 경우 (분류기 우회) ──
    Node("source_pin", "소스 지정 경로",
         fn=f"{_ORCH}.OrchestratorAgent._allowed_routes",
         knobs=("orchestrator._SOURCE_ROUTE_MAP",)),
    Node("followup", "후속 경로 상속", fn=f"{_ORCH}._inherit_route_for_followup",
         knobs=("orchestrator._METRIC_NOUNS", "orchestrator._ROUTE_MARKERS",
                "orchestrator._BIGQUERY_CORRECTION_MARKERS")),

    # ── 분류기 ──
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
    Node("route.multi", "multi", group="route"),
    Node("route.model_rights", "model_rights", group="route"),
    Node("route.report", "report", group="route"),
    # ⚠️ 화살표 없이 홀로 뜬다 — **그것이 사실이다.** `_handle_team` 과 두 곳의
    #    디스패치 배선은 살아 있는데, 어떤 진입점도 `team` 을 만들지 않는다
    #    (분류기 밖 + `_DB_REGISTRY` 에 엔트리 없음). orchestrator.py 안에도 같은
    #    내용이 주석으로 적혀 있다. 지우면 죽은 배선이 조용히 남고, 화살표를
    #    그리면 거짓말이 된다 — 세 번째 선택지가 '이유를 달아 도달 불가로 표시' 다.
    Node("route.team", "team", group="route",
         unreachable="어떤 진입점도 이 경로를 만들지 않는다 "
                     "(분류기 밖 · @@ 레지스트리에 엔트리 없음)"),

    # ⛔ 여기 있던 "모든 경로 → 답변 수치검증" 은 거짓이었다. 호출부는 앱 전체에
    #    하나뿐이라(`sql_agent.format_answer` 안) bigquery 하위 그래프의 이탈
    #    노드에만 붙는다 — `build()` 가 이탈점을 읽어 잇는다.
    Node("answer_check", "답변 수치검증 (bigquery 전용)",
         fn="app.core.answer_check.log_verification"),
    Node("response", "응답", group="io"),
)

_ROUTE_IDS = tuple(n.id for n in NODES if n.group == "route" and not n.unreachable)

# 분류기가 낼 수 있는 여섯 (orchestrator.ROUTER_ROUTES 와 같아야 한다 — 검사가 본다)
_CLASSIFIER_ROUTES = ("route.bigquery", "route.notion", "route.gws",
                      "route.cs", "route.multi", "route.direct")

# `@@` 지정·사이드바 단일 소스가 곧장 보내는 경로.
# `_DB_REGISTRY` 의 라우트 6종 중 남는 것이 이 넷인데, **둘의 이유가 서로 다르다.**
#
#   · `model_rights` — 위 관문이 정말로 채간다. 관문 조건에
#     `list(enabled_sources) == ["초상권"]` 이 들어 있어 소스 지정도 거기서 걸린다.
#   · `report` — 관문이 채가지 **않는다.** 관문은 `db_entry`(질문에 적은 `@@보고서`)와
#     `wants_report(query)` 문구만 본다 — `enabled_sources` 는 쳐다보지 않는다.
#     그래서 `/보고서` 로 소스만 지정하고 본문에 '보고서' 를 안 쓰면 관문을 그냥
#     통과하고, 여기 소스 지정 경로가 `route = "report"` 를 만든다.
#
# ⛔ 그런데도 `report` 를 여기 넣지 않는 이유는 **그 경로가 보고서를 만들지 않기
#    때문**이다. 하류 디스패치 표 어디에도 `report` 가 없어 (`HANDLER_ROUTES` 로
#    만드는 표·스트리밍 말미의 `{"gws","team"}` 표 둘 다) `handler` 가
#    `_handle_direct` 로 떨어진다 — 라우트 변수만 `report` 이고 실제로 나가는 것은
#    평범한 direct 답변이다. `route.report` 노드는 "보고서 생성이 실행된다" 는 뜻이라,
#    여기에 화살표를 그으면 없는 산출물을 약속하는 거짓 엣지가 하나 더 생긴다.
#    (핸들러가 direct 로 강등되는 것 자체는 별건의 잠복 결함 — 이 커밋 범위 밖이다.)
#
# 2026-08-24 재리뷰: 예전 주석은 이 자리에서 "관문이 이미 채간다" 고 적어
# `report` 까지 싸잡았는데 그건 사실이 아니었다. 그린 엣지는 지금도 전부 맞지만,
# **코드가 부정하는 손글씨 근거**는 거짓 엣지 13개를 낳은 것과 같은 부류라 고쳤다.
_PINNED_ROUTES = ("route.bigquery", "route.notion", "route.cs", "route.gws")

# 직전 경로 상속이 낼 수 있는 값 = `_ROUTE_MARKERS` 의 네 종 (+ 정정 후속 → bigquery)
_INHERITED_ROUTES = ("route.bigquery", "route.notion", "route.gws", "route.cs")

EDGES: tuple[Edge, ...] = (
    Edge("input", "at_parse"),

    Edge("at_parse", "intercept.model_rights"),
    Edge("intercept.model_rights", "route.model_rights",
         label="@@초상권 · 초상권 의도", conditional=True),
    Edge("intercept.model_rights", "intercept.report", label="통과", conditional=True),

    Edge("intercept.report", "route.report",
         label="@@보고서 · 보고서 요청", conditional=True),
    Edge("intercept.report", "source_pin", label="통과", conditional=True),

    *tuple(Edge("source_pin", r, label="소스 지정", conditional=True)
           for r in _PINNED_ROUTES),
    Edge("source_pin", "followup", label="지정 없음", conditional=True),

    *tuple(Edge("followup", r, label="직전 경로 상속", conditional=True)
           for r in _INHERITED_ROUTES),
    Edge("followup", "router.keyword", label="후속 아님", conditional=True),

    Edge("router.keyword", "router.llm", label="확신 없음", conditional=True),
    *tuple(Edge("router.keyword", r, label="확신", conditional=True)
           for r in _CLASSIFIER_ROUTES),
    *tuple(Edge("router.llm", r, conditional=True) for r in _CLASSIFIER_ROUTES),

    # bigquery 만 하위 그래프를 거친다. `build()` 가 이 직행 엣지를 떼고
    # `route.bigquery → generate_sql … format_answer → answer_check` 로 다시 잇는다.
    # ⚠️ `multi` 도 `_multi_prepare` 를 통해 sql_agent 를 부르지만, 그 답은 Flash
    #    합성을 한 번 더 거쳐 나간다 — 최종 답변이 검증됐다는 뜻이 아니므로
    #    `route.multi → answer_check` 를 그리지 않는다.
    Edge("route.bigquery", "answer_check"),
    Edge("answer_check", "response"),
    *tuple(Edge(r, "response") for r in _ROUTE_IDS if r != "route.bigquery"),
)
