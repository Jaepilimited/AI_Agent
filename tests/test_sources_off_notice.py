"""소스가 꺼져 있어 조회를 건너뛴 사실은 코드가 말한다 (2026-09-15 붐따 #174·#175·#177).

배경: System Status 의 소스 선택이 실제 라우팅에 닿게 된 뒤(2026-09-03), `전체 해제`
해 둔 사람의 매출 질문이 확신 `bigquery` 로 분류되고도 **조용히** `direct` 로 떨어졌다.
필터 로그는 INFO 라 프로덕션에서 버려졌고, LLM 은 "조회가 붙지 않았다" 며 변명하거나
근거 없는 수치(390.2억)를 말했다. 9/3 전후 실측: 1.9%(5명) → 18.2%(13명).

⛔ 개수를 세는 배선 검사는 방어가 아니다 — 여기서는 문구 자체와, 실제 사고 문장이
   같은 판정 경로를 지날 때 공시가 나오는지를 본다. 배선은 두 경로가 **같은 자리**에
   있는지만 확인한다.
"""
import re
from pathlib import Path

import pytest

from app.agents import orchestrator as orch
from app.agents.orchestrator import OrchestratorAgent, _sources_off_notice

SRC = Path(orch.__file__).read_text(encoding="utf-8")

# 붐따 #174 의 실제 질문
INCIDENT_QUESTION = "2026년 브랜드 SK 남미 국가별 매출과 비중을 매출 높은 순으로 알려줘"


@pytest.fixture(scope="module")
def agent():
    return OrchestratorAgent()


# ── 문구 ──────────────────────────────────────────────────────────────────

def test_notice_names_the_gap_and_the_two_ways_out():
    note = _sources_off_notice("bigquery", [])
    assert "데이터 조회를 실행하지 않았습니다" in note
    assert "전체 해제 상태" in note
    assert "System Status" in note and "전체 선택" in note
    assert "@@매출" in note
    # 답변 속 수치를 조회 결과로 읽지 않게 못 박는다 — 실제로 LLM 이 390.2억을 지어냈다
    assert "조회 결과가 아닙니다" in note


def test_notice_reports_partial_selection_count():
    note = _sources_off_notice("bigquery", ["B2B1", "OP"])
    assert "2개만 선택된 상태" in note
    assert "전체 해제" not in note


def test_no_notice_for_server_default():
    # None = 서버 기본값. 사용자가 끈 것이 아니라 설계된 기본이라 매번 뜨면 소음이다
    assert _sources_off_notice("bigquery", None) == ""
    assert _sources_off_notice("notion", None) == ""


def test_no_notice_for_routes_without_a_source():
    assert _sources_off_notice("direct", []) == ""
    assert _sources_off_notice("", []) == ""


@pytest.mark.parametrize("route, hint", [
    ("multi", "@@매출"),
    ("gws", "@@gws"),
    ("cs", "@@BP"),
    ("inventory", "@@OP"),
    ("awards", "@@수상"),
    ("report", "@@보고서"),
])
def test_notice_hint_matches_the_route(route, hint):
    assert hint in _sources_off_notice(route, [])


# ── 사고 재현: 같은 판정 경로 ────────────────────────────────────────────

def test_incident_question_is_confident_bigquery_and_empty_selection_blocks_it(agent):
    route, confident = agent._keyword_classify_ex(INCIDENT_QUESTION)
    assert (route, confident) == ("bigquery", True)
    allowed = agent._allowed_routes([])
    assert route not in allowed, "전체 해제면 매출 경로가 막힌다 — 그래서 공시가 필요하다"
    assert _sources_off_notice(route, []), "막혔으면 코드가 말해야 한다"


def test_selection_that_keeps_sales_source_raises_no_notice(agent):
    route, _ = agent._keyword_classify_ex(INCIDENT_QUESTION)
    allowed = agent._allowed_routes(["매출", "제품"])
    assert route in allowed
    # 막히지 않았으니 공시할 일도 없다 (호출부는 필터 안에서만 부른다)


# ── 배선: 두 경로가 같은 자리에서 같은 일을 한다 ─────────────────────────

def test_both_paths_compute_the_notice_inside_the_source_filter():
    # 필터가 route 를 direct 로 뒤집기 **직전**에 판정해야 원래 경로를 안다
    hits = re.findall(r"_sources_off_note = _sources_off_notice\(route, enabled_sources\)", SRC)
    assert len(hits) == 2, "비스트리밍·스트리밍 필터 양쪽에서 판정해야 한다"


def test_filter_logs_at_warning_when_user_narrowed_sources():
    # INFO 는 프로덕션에서 버려진다 — 이 필터가 13명의 질문을 떨어뜨리는 동안 흔적이 없었다
    for path in ("route_and_execute", "route_and_stream"):
        assert re.search(
            r'logger\.warning\("route_filtered_by_sources", path="' + path + r'"', SRC
        ), f"{path}: 사용자가 좁힌 소스로 막힌 경우는 WARNING 이어야 한다"


def test_notice_is_prepended_in_both_paths():
    # 비스트리밍: direct 결과 answer 맨 앞
    assert re.search(
        r'result\["answer"\] = _sources_off_note \+ "\\n\\n" \+ result\["answer"\]', SRC
    )
    # 스트리밍: direct 첫 청크
    assert re.search(r'yield \("chunk", _sources_off_note \+ "\\n\\n"\)', SRC)


def test_helper_lives_outside_the_class():
    # ⛔ 클래스 안에 모듈 레벨 def 를 넣으면 거기서 클래스가 끝난다 (2026-09-03 실제 사고)
    assert SRC.index("def _sources_off_notice(") < SRC.index("class OrchestratorAgent")


# ── 동작: 비스트리밍 경로에서 실제로 앞에 붙는다 ──────────────────────────

async def _no_report(*args, **kwargs):
    return None


@pytest.mark.asyncio
async def test_non_streaming_direct_answer_starts_with_notice_when_sales_source_is_off():
    agent = OrchestratorAgent.__new__(OrchestratorAgent)

    async def direct(*args, **kwargs):
        return {"source": "direct", "answer": "direct answer"}

    async def bigquery(*args, **kwargs):
        raise AssertionError("disabled bigquery route executed")

    agent._handle_direct = direct
    agent._handle_bigquery = bigquery
    agent._handle_report = _no_report
    agent.parse_db_prefix = lambda query: (None, query)
    agent._keyword_classify_ex = lambda query: ("bigquery", True)

    result = await agent.route_and_execute(INCIDENT_QUESTION, enabled_sources=[])

    assert result["source"] == "direct"
    assert result["answer"].startswith("> ⚠️ **데이터 조회를 실행하지 않았습니다.**")
    assert "@@매출" in result["answer"]
    assert result["answer"].endswith("direct answer")


@pytest.mark.asyncio
async def test_non_streaming_direct_answer_is_untouched_under_server_default():
    # 서버 기본값(None)으로 direct 가 된 것은 사용자가 끈 것이 아니다 — 공시 없음
    agent = OrchestratorAgent.__new__(OrchestratorAgent)

    async def direct(*args, **kwargs):
        return {"source": "direct", "answer": "direct answer"}

    agent._handle_direct = direct
    agent._handle_report = _no_report
    agent.parse_db_prefix = lambda query: (None, query)
    agent._keyword_classify_ex = lambda query: ("direct", True)

    result = await agent.route_and_execute("안녕", enabled_sources=None)
    assert result["answer"] == "direct answer"
