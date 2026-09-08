# -*- coding: utf-8 -*-
"""제품 라인을 지목한 질문은 **제품 Q&A(BP)** 로 간다 — 2026-09-04.

⛔ **실사용 제보**: *"왜 노션정보만 가져오지?"*

    질문: "히알루테카 제품 정보 알려줘"
    답변: "…아래의 사내 노션 문서에서 확인하실 수 있습니다.
           경로: 스킨1004 전제품 한 눈에 파악하기 > New database > 히알루-테카 (New)
           **검색된 문서 내에 구체적인 제품 스펙이나 성분 등의 상세 텍스트는
           포함되어 있지 않으나** …"

**경로와 링크만 주고 내용은 없었다.** 원인은 데이터가 아니라 **라우팅**이었다 —
`제품 정보` 가 `_NOTION_KEYWORDS` 에 들어 있고 노션이 CS 보다 먼저 판정되어,
`히알루` 가 `_CS_KEYWORDS` 에 있는데도 노션이 이겼다.

⚠️ **데이터는 있었다** (실측):
   - CS 시트: 히알루-테카 Q&A 5건 (퍼밍 크림 이물질 문의·플럼핑 앰플 아시아틱애씨드
     함량·동물성 원료 무첨가 여부 등)
   - 전성분 테이블: 센텔라 테카 4종 + 히알루-테카 플럼핑 앰플 (767~1,713자)

⚠️ **후속 발화는 직전 경로를 물려받는다.** 그래서 "히알루-테카 퍼밍 크림은?" 도 함께
   노션으로 갔다 — **첫 질문이 틀리면 대화 전체가 틀린다.**
"""
import pytest


def _route(q: str):
    from app.agents.orchestrator import OrchestratorAgent

    o = OrchestratorAgent.__new__(OrchestratorAgent)
    return OrchestratorAgent._keyword_classify_ex(o, q)


@pytest.mark.parametrize("q", [
    "히알루테카 제품 정보 알려줘",
    "히알루-테카 퍼밍 크림은?",
    "센텔라 테카 제품 정보",
    "센텔라 앰플 성분 알려줘",
    "프로바이오시카 제품 정보 알려줘",
])
def test_named_product_line_goes_to_product_qa(q):
    """⛔ 라인을 지목했으면 노션 문서가 아니라 제품 Q&A 다."""
    route, _ = _route(q)
    assert route == "cs", (q, route)


@pytest.mark.parametrize("q", [
    "노션에서 히알루테카 제품 정보 찾아줘",
    "제품 정보 문서 어디 있어",
    "반품 정책 알려줘",
    "시딩 가이드라인 알려줘",
])
def test_document_questions_still_go_to_notion(q):
    """⚠️ 반대 방향 — 노션을 명시했거나 문서를 찾는 질문은 그대로 노션이다.

    좁히려다 넓히면 사내 문서 검색이 통째로 죽는다.
    """
    route, _ = _route(q)
    assert route == "notion", (q, route)


def test_line_plus_data_words_still_go_to_bigquery():
    """⚠️ '센텔라 라인 매출' 은 제품 Q&A 가 아니라 조회다."""
    route, _ = _route("센텔라 라인 매출 알려줘")
    assert route == "bigquery"


def test_line_vocabulary_comes_from_the_single_source():
    """⛔ 라인 어휘를 라우터에 손으로 적지 마라 — 프롬프트 표가 단일 소스다.

    실제로 그렇게 적어 둔 탓에 센텔라테카·히알루테카가 빠져 있었던 전례가 있다
    (`INGREDIENT_EXCLUSION_MESSAGE`, 2026-09-03).
    """
    from app.agents.orchestrator import _product_line_named

    assert _product_line_named("히알루테카 제품 정보") == {"히알루테카"}
    assert _product_line_named("센텔라 테카 앰플") == {"센텔라테카"}
    assert _product_line_named("반품 정책") == set()


def test_helper_lives_outside_the_class():
    """⚠️ 클래스 안에 모듈 레벨 `def` 를 넣으면 거기서 클래스 본문이 끊긴다
    (2026-09-03 실제 배포 사고)."""
    from pathlib import Path

    src = (Path(__file__).resolve().parent.parent
           / "app" / "agents" / "orchestrator.py").read_text(encoding="utf-8")
    assert src.index("def _product_line_named") < src.index("class OrchestratorAgent")


# ── 후속 발화가 물려받는 경로 (2026-09-04) ──────────────────────────────────
#
# ⛔ 첫 질문을 CS 로 고쳤는데도 두 번째 "히알루-테카 퍼밍 크림은?" 이 notion 으로 갔다.
#    `_ROUTE_MARKERS` 의 notion 표지에 일반적인 `"출처:"` 가 들어 있었고,
#    CS 답변 꼬리가 `*출처: CS 제품 Q&A 데이터베이스*` 라서 **CS 답변이 notion 으로
#    읽혔다.** notion 이 cs 보다 먼저 검사되므로 cs 항목까지 가지도 못한다.

def test_cs_answer_is_not_mistaken_for_notion():
    """⛔ 실패의 핵심 — CS 꼬리의 `출처:` 가 notion 표지에 걸렸다."""
    from app.agents.orchestrator import _previous_route

    cs_tail = "AI: 제품 정보입니다\n---\n*출처: CS 제품 Q&A 데이터베이스*"
    assert _previous_route(cs_tail) == "cs"


def test_each_route_marker_is_unique_to_that_route():
    """⚠️ 표지는 **그 경로에서만** 나오는 문자열이어야 한다.
    실패 답변에도 들어가는 낱말은 표지가 될 수 없다 (골든셋 기대어 규칙과 같다)."""
    from app.agents.orchestrator import _ROUTE_MARKERS

    # ⚠️ 길이로 판정하지 않는다 — `[메일]`(4자)은 짧아도 그 경로에서만 나온다.
    #    불변식은 **서로 삼키지 않는 것**이다.
    flat = [(r, m) for r, ms in _ROUTE_MARKERS for m in ms]
    for route, marker in flat:
        for other_route, other in flat:
            if other_route == route:
                continue
            assert marker not in other and other not in marker, \
                f"{route}:{marker!r} 와 {other_route}:{other!r} 가 서로를 삼킨다"

    # 실제 답변 꼬리로 한 번 더 — 각 경로의 꼬리가 자기 경로로만 읽혀야 한다
    from app.agents.orchestrator import _previous_route
    for want, tail in {"cs": "*출처: CS 제품 Q&A 데이터베이스*",
                       "notion": "*Notion 사내 문서 검색 · CS 팀 자료*"}.items():
        assert _previous_route("AI: 답\n" + tail) == want, tail


@pytest.mark.parametrize("tail,want", [
    ("AI: 답\n*Notion 사내 문서 검색 · CS 팀 자료*", "notion"),
    ("AI: 답\n*출처: CS 제품 Q&A 데이터베이스*", "cs"),
    ("AI: 답\n[직전 실행 SQL: SELECT 1]", "bigquery"),
])
def test_previous_route_reads_the_right_marker(tail, want):
    from app.agents.orchestrator import _previous_route

    assert _previous_route(tail) == want
