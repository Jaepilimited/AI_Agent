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
    gate: str = ""                 # 관문이면 orchestrator 안의 **호출부 표식**.
    #   ⛔ `fn`(결정 함수)과 다를 수 있다 — 보고서는 `wants_report` 로 판정하지만
    #      호출부는 `_handle_report` 다. 그래서 둘을 따로 둔다.
    #   ⚠️ 이 표식이 두 경로(비스트리밍·스트리밍)에서 안 보이면
    #      `flow_gates_match_code` 가 **실패로 올린다** — 조용히 낡지 않는다.
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
    # 사내 은어·오타 보정 — 라우팅/SQL/캐시/성분 조회 **전에 한 번만** 돈다.
    # 질문 문자열을 바꾸므로 뒤의 모든 판정이 이 결과를 본다.
    Node("alias_expand", "은어·오타 보정",
         fn="app.core.term_aliases.expand_aliases",
         knobs=("term_aliases 사전",),
         gate='expand_aliases('),
    Node("at_parse", "@@ 소스 파싱", fn=f"{_ORCH}.OrchestratorAgent.get_db_registry",
         # `|` 로 나눈 여러 표식 — 이 노드는 파싱과 `@@` 명령 응답을 함께 맡는다
         gate="parse_db_prefix(|if db_entry:",
         knobs=("orchestrator._DB_REGISTRY",)),

    # ── 분류기보다 **먼저** 도는 관문 ──
    # 두 경로 모두 `route_and_execute`(비스트리밍) 와 `route_and_stream`(스트리밍)
    # 에서 분류기 호출보다 위에 있고, 걸리면 그 자리에서 return 한다.
    # ⛔ **여기 순서가 곧 실행 순서다.** 앞의 관문이 먼저 잡으면 뒤는 못 본다 —
    #    그림의 순서가 코드와 다르면 캔버스가 조용히 거짓말을 한다
    #    (2026-09-09: 실제로 4개가 코드와 다른 순서로 그려지고 있었고,
    #     6개는 아예 빠져 있었다. `flow_gates_match_code` 가 이제 매일 대조한다).
    Node("intercept.notion_save", "노션 저장 관문",
         fn="app.core.notion_save.handle",
         knobs=("저장 동사 + 노션 낱말", "NOTION_WRITE_TOKEN"),
         gate='notion_save.handle'),
    Node("intercept.cs_order_scope", "국내 CS 주문 소스 확인",
         fn=f"{_ORCH}._cs_order_routing",
         knobs=("국내CS 선택 범위",),
         gate='_cs_order_routing('),
    Node("intercept.dashboard_link", "대시보드 링크 관문",
         fn="app.core.dashboard_links.answer_dashboard_link_query",
         knobs=("dashboard 카탈로그 JSON",),
         gate='answer_dashboard_link_query('),
    Node("intercept.team_country", "팀 담당 국가 관문",
         fn="app.core.org_structure.answer_team_country_scope",
         knobs=("org_structure 등록 팀", "권역명은 되묻는다"),
         gate='answer_team_country_scope('),
    Node("intercept.clarify", "부정-only 되묻기",
         fn="app.core.route_intent.clarify_message",
         knobs=("settings.bare_rejection_clarify_enabled",),
         gate='clarify_message('),
    Node("intercept.company", "회사 사실 관문",
         fn="app.core.company_facts.answer",
         knobs=("company_facts 표",),
         gate='_company_answer('),
    # ⛔ 손익(FI) 방어선 1번 — **LLM 을 부르기 전에** 거절한다.
    #    나머지 4겹(프롬프트 마스킹·테이블 화이트리스트·validate_sql·프론트)이
    #    있어도 이 관문이 가장 앞이라 캔버스에 없으면 방어선이 안 보인다.
    Node("intercept.fi_guard", "손익 권한 거절",
         fn=f"{_ORCH}._requests_fi_data",
         gate="_requests_fi_data(",
         knobs=("directory_users.can_view_fi",)),
    Node("intercept.ingredient", "성분 필터 가로채기",
         fn=f"{_ORCH}._ingredient_filter_intent",
         knobs=("성분 포함/미포함 낱말",),
         gate='_ingredient_filter_intent('),
    # ⛔ 유통기한은 **재고보다 먼저** 판정한다 — 둘 다 '재고' 를 신호로 쓰기 때문이다
    #    (로트 잔량과 창고 재고는 세는 기준이 다르다).
    Node("intercept.expiry", "유통기한 가로채기",
         fn=f"{_ORCH}.OrchestratorAgent._expiry_term",
         knobs=("'유통기한'·'임박' 낱말",),
         gate='_expiry_term('),
    # 재고 가로채기 — 성분과 같은 이유로 분류기보다 **먼저** 돈다. 재고는 표 조회라
    #  LLM 이 SQL 을 짜게 두지 않는다 (2026-08-25).
    Node("intercept.inventory", "재고 가로채기",
         fn="app.core.inventory.inventory_intent",
         knobs=("@@OP 지정", "'재고' 낱말"),
         gate='_inventory_term('),
    # 수상/랭킹 가로채기 — 재고·성분과 같은 이유로 분류기보다 **먼저** 돈다.
    # 랭킹이 숫자·판정이라 LLM 에 SQL 을 맡기지 않는다 (2026-09-07).
    Node("intercept.awards", "수상/랭킹 가로채기",
         fn="app.core.awards.awards_intent",
         knobs=("@@수상 지정", "'수상'·'어워드' 낱말"),
         gate='_awards_term(|_handle_awards_query('),
    Node("intercept.model_rights", "초상권 가로채기",
         fn="app.core.model_rights.model_rights_intent",
         knobs=("@@초상권 지정", "사진 첨부 시 얼굴 인식"),
         gate='model_rights_intent'),
    # 이미지가 붙으면 분류를 건너뛰고 vision LLM 으로 간다 — 어떤 라우트도
    # 사진을 읽지 못하므로 여기서 갈라야 한다.
    Node("intercept.images", "이미지 첨부 → direct 강제",
         fn=f"{_ORCH}.OrchestratorAgent._handle_direct",
         gate="if images:",
         knobs=("첨부 이미지 유무",)),
    Node("intercept.report", "보고서 가로채기",
         fn="app.reports.registry.wants_report",
         knobs=("registry._REPORT_META", "@@보고서 지정"),
         gate='_handle_report('),

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

    Node("route_filter.allowed", "enabled-source filter",
         fn=f"{_ORCH}.OrchestratorAgent._allowed_routes",
         knobs=("orchestrator._SOURCE_ROUTE_MAP",)),

    Node("multi_prefix.fanout", "@@ multi-source parallel fan-out",
         fn=f"{_ORCH}.OrchestratorAgent._build_multi_prefix_tasks",
         knobs=("orchestrator.MULTI_PREFIX_ROUTE_TARGETS",)),
    Node("multi_prefix.merge", "@@ multi-source merge", group="main"),

    Node("route.direct", "direct", group="route"),
    Node("route.bigquery", "bigquery", group="route",
         subgraph="app.agents.sql_agent.build_sql_agent_graph"),
    Node("route.notion", "notion", group="route"),
    Node("route.cs", "cs", group="route"),
    Node("route.gws", "gws", group="route"),
    Node("route.multi", "multi", group="route"),
    Node("route.model_rights", "model_rights", group="route"),
    Node("route.report", "report", group="route"),
    Node("route.inventory", "inventory", group="route"),
    Node("route.awards", "awards", group="route"),
    # ⚠️ 여기 있던 `route.team` 은 2026-08-25 에 **배선째 걷어냈다.** 도달 불가 표시는
    #    "지우지도 화살표를 긋지도 않는" 세 번째 선택지였는데, 코드를 실제로 지운
    #    다음에는 그 표시 자체가 낡은 사실이 된다. 팀 자료는 이제 벡터 색인의 링크
    #    카드(`app/core/team_link_index.py`)로 `notion` 경로가 답한다.
    #    ⛔ 되살릴 생각이면 `team_resources` 표부터 보라 — 그건 지우지 않았고,
    #       지금 링크 카드가 그 표를 먹고 산다.

    # ⛔ 여기 있던 "모든 경로 → 답변 수치검증" 은 거짓이었다. bigquery 하위 그래프의
    #    이탈 노드에만 붙는다 — `build()` 가 이탈점을 읽어 잇는다.
    # ⚠️ 2026-08-31 부터 스트리밍 경로(`sql_agent.run_sql_agent_stream`)도 같은
    #    검증을 부른다(`_number_check_notice`). 그건 LangGraph 노드가 아니라 여기
    #    엣지로 그릴 자리가 없다 — **덜 그리는 것은 괜찮고, 없는 화살표를 그리는
    #    것만 안 된다.** (배선이 `format_answer` 하나뿐이던 시절, 실사용 채팅은
    #    스트리밍이라 검증이 통째로 안 돌았다. 이 그림은 그때도 맞았다.)
    Node("answer_check", "답변 수치검증 (bigquery 전용)",
         fn="app.core.answer_check.log_verification"),
    Node("response", "응답", group="io"),
)

_ROUTE_IDS = tuple(n.id for n in NODES if n.group == "route" and not n.unreachable)

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
#    만드는 표 · 스트리밍 말미의 디스패치 둘 다) `handler` 가
#    `_handle_direct` 로 떨어진다 — 라우트 변수만 `report` 이고 실제로 나가는 것은
#    평범한 direct 답변이다. `route.report` 노드는 "보고서 생성이 실행된다" 는 뜻이라,
#    여기에 화살표를 그으면 없는 산출물을 약속하는 거짓 엣지가 하나 더 생긴다.
#    (핸들러가 direct 로 강등되는 것 자체는 별건의 잠복 결함 — 이 커밋 범위 밖이다.)
#
# 2026-08-24 재리뷰: 예전 주석은 이 자리에서 "관문이 이미 채간다" 고 적어
# `report` 까지 싸잡았는데 그건 사실이 아니었다. 그린 엣지는 지금도 전부 맞지만,
# **코드가 부정하는 손글씨 근거**는 거짓 엣지 13개를 낳은 것과 같은 부류라 고쳤다.
_PINNED_ROUTES = ("route.bigquery", "route.notion", "route.cs", "route.gws")

# 직전 경로 상속이 낼 수 있는 값 = `_ROUTE_MARKERS` 의 다섯 종 (+ 정정 후속 → bigquery)
# ⛔ `awards` 는 위의 `report` 함정을 그대로 밟을 뻔했다 — `HANDLER_ROUTES` 에 없어
#    `_resolve_handler` 가 그냥 두면 direct 로 강등된다. `report` 와 달리 여기서는
#    `route_and_execute`/`route_and_stream` 양쪽 디스패치에 `route == "awards"` 분기를
#    **따로 추가해** `_handle_awards_query` 를 실제로 태우므로(2026-09-07), 이 엣지는
#    거짓이 아니다. 분기를 없애면 이 줄도 함께 지울 것 — 그러지 않으면 `report` 가
#    겪었던 것과 같은 "그림과 코드가 갈리는" 상태가 된다.
_INHERITED_ROUTES = ("route.bigquery", "route.notion", "route.gws", "route.cs", "route.awards")


def classifier_routes() -> tuple[str, ...]:
    """Read the classifier universe at graph-build time, not from a copied list."""
    from app.agents.orchestrator import ROUTER_ROUTES

    return tuple(f"route.{route}" for route in sorted(ROUTER_ROUTES))


_DIRECT_FILTER_LABEL = "native direct or disallowed → direct"


def direct_filter_label_is_truthful(label: str) -> bool:
    """A shared direct edge must describe both ways it receives traffic."""
    normalized = label.lower()
    return "native direct" in normalized and "disallowed" in normalized


def multi_prefix_targets() -> tuple[str, ...]:
    """Read @@ fan-out targets from the same map used by orchestrator dispatch."""
    from app.agents.orchestrator import multi_prefix_target_routes

    return multi_prefix_target_routes()


def generated_nodes() -> tuple[Node, ...]:
    return tuple(
        Node(f"multi_prefix.branch.{target}", f"@@ branch: {target}", group="branch")
        for target in multi_prefix_targets()
    )


def generated_edges() -> tuple[Edge, ...]:
    routes = classifier_routes()
    return (
        Edge("router.keyword", "router.llm", label="not confident", conditional=True),
        Edge("router.keyword", "route_filter.allowed", label="confident", conditional=True),
        Edge("router.llm", "route_filter.allowed", label="classified", conditional=True),
        *tuple(
            Edge("route_filter.allowed", route,
                 label=_DIRECT_FILTER_LABEL if route == "route.direct" else "allowed",
                 conditional=True)
            for route in routes
        ),
        Edge("source_pin", "multi_prefix.fanout", label="@@ multi-source", conditional=True),
        *tuple(
            Edge("multi_prefix.fanout", f"multi_prefix.branch.{target}",
                 label="parallel", conditional=True)
            for target in multi_prefix_targets()
        ),
        *tuple(
            Edge(f"multi_prefix.branch.{target}", "multi_prefix.merge")
            for target in multi_prefix_targets()
        ),
        Edge("multi_prefix.merge", "response"),
    )


def all_nodes() -> tuple[Node, ...]:
    return NODES + generated_nodes()


def all_edges() -> tuple[Edge, ...]:
    """Graph edges with dynamic classifier and @@ fan-out sections."""
    return EDGES + generated_edges()

EDGES: tuple[Edge, ...] = (
    # ⛔ 이 사슬의 **순서가 곧 코드의 순서**다 (orchestrator.route_and_execute /
    #    route_and_stream). 하나라도 어긋나면 캔버스가 거짓말을 한다.
    Edge("input", "intercept.notion_save"),
    Edge("intercept.notion_save", "route.direct",
         label="저장 요청 · 저장함", conditional=True),
    Edge("intercept.notion_save", "alias_expand", label="통과", conditional=True),

    Edge("alias_expand", "at_parse"),

    Edge("at_parse", "intercept.cs_order_scope"),
    Edge("intercept.cs_order_scope", "route.direct",
         label="국내CS 미선택 · 조회 없이 안내", conditional=True),
    Edge("intercept.cs_order_scope", "intercept.dashboard_link", label="통과", conditional=True),
    Edge("intercept.dashboard_link", "route.direct",
         label="대시보드 링크 질문", conditional=True),
    Edge("intercept.dashboard_link", "intercept.team_country", label="통과", conditional=True),

    Edge("intercept.team_country", "route.direct",
         label="등록 팀의 담당 국가", conditional=True),
    Edge("intercept.team_country", "intercept.clarify", label="통과", conditional=True),

    Edge("intercept.clarify", "route.direct",
         label="부정만 있고 정보 없음 · 되묻기", conditional=True),
    Edge("intercept.clarify", "intercept.company", label="통과", conditional=True),

    Edge("intercept.company", "route.direct",
         label="회사 기본 사실", conditional=True),
    Edge("intercept.company", "intercept.fi_guard", label="통과", conditional=True),

    Edge("intercept.fi_guard", "route.bigquery",
         label="권한 없음 · 거절", conditional=True),
    Edge("intercept.fi_guard", "intercept.ingredient", label="통과", conditional=True),

    Edge("intercept.ingredient", "route.bigquery",
         label="성분 포함/미포함 질문", conditional=True),
    Edge("intercept.ingredient", "intercept.expiry", label="통과", conditional=True),

    Edge("intercept.expiry", "route.inventory",
         label="유통기한 질문 (재고보다 먼저)", conditional=True),
    Edge("intercept.expiry", "intercept.inventory", label="통과", conditional=True),

    Edge("intercept.inventory", "route.inventory",
         label="@@OP · 재고 질문", conditional=True),
    Edge("intercept.inventory", "intercept.awards", label="통과", conditional=True),

    Edge("intercept.awards", "route.awards",
         label="@@수상 · 수상/랭킹 질문", conditional=True),
    Edge("intercept.awards", "intercept.model_rights", label="통과", conditional=True),

    Edge("intercept.model_rights", "route.model_rights",
         label="@@초상권 · 초상권 의도", conditional=True),
    Edge("intercept.model_rights", "intercept.report", label="통과", conditional=True),

    Edge("intercept.report", "route.report",
         label="@@보고서 · 보고서 요청", conditional=True),
    Edge("intercept.report", "intercept.images", label="통과", conditional=True),

    Edge("intercept.images", "route.direct",
         label="사진 첨부 · vision", conditional=True),
    Edge("intercept.images", "source_pin", label="통과", conditional=True),

    *tuple(Edge("source_pin", r, label="소스 지정", conditional=True)
           for r in _PINNED_ROUTES),
    Edge("source_pin", "followup", label="지정 없음", conditional=True),

    *tuple(Edge("followup", r, label="직전 경로 상속", conditional=True)
           for r in _INHERITED_ROUTES),
    Edge("followup", "router.keyword", label="후속 아님", conditional=True),

    # bigquery 만 하위 그래프를 거친다. `build()` 가 이 직행 엣지를 떼고
    # `route.bigquery → generate_sql … format_answer → answer_check` 로 다시 잇는다.
    # ⚠️ `multi` 도 `_multi_prepare` 를 통해 sql_agent 를 부르지만, 그 답은 Flash
    #    합성을 한 번 더 거쳐 나간다 — 최종 답변이 검증됐다는 뜻이 아니므로
    #    `route.multi → answer_check` 를 그리지 않는다.
    Edge("route.bigquery", "answer_check"),
    Edge("answer_check", "response"),
    *tuple(Edge(r, "response") for r in _ROUTE_IDS if r != "route.bigquery"),
)
