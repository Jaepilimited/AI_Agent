"""Deterministic company-structure answers for verified team scopes."""

import pytest

try:
    from app.core import org_structure
except ImportError:  # Keep RED as an assertion failure instead of collection failure.
    org_structure = None

from app.agents.orchestrator import OrchestratorAgent


def _org_module():
    assert org_structure is not None, "company structure answer module is missing"
    return org_structure


@pytest.mark.parametrize(
    ("query", "included", "excluded"),
    [
        (
            "동남아시아2팀이 담당하는 국가가 어디지?",
            ("동남아시아2팀(EAST2)", "말레이시아", "싱가포르"),
            ("인도네시아", "필리핀"),
        ),
        (
            "EAST2 담당 국가는?",
            ("동남아시아2팀(EAST2)", "말레이시아", "싱가포르"),
            ("인도네시아",),
        ),
        (
            "동남아1팀은 어느 나라를 담당해?",
            ("동남아시아1팀(EAST1)", "인도네시아", "필리핀"),
            ("말레이시아", "싱가포르"),
        ),
    ],
)
def test_verified_team_country_scope_is_answered_exactly(query, included, excluded):
    answer = _org_module().answer_team_country_scope(query)

    assert answer is not None
    for value in included:
        assert value in answer
    for value in excluded:
        assert value not in answer


@pytest.mark.parametrize(
    "query",
    [
        "동남아시아2팀의 8월 국가별 매출 알려줘",
        "동남아시아2팀 광고비와 ROAS를 국가별로 보여줘",
        "동남아시아2팀이 담당하는 국가의 이번 달 매출은?",
        "동남아시아2팀에 누가 있어?",
    ],
)
def test_data_or_people_questions_are_not_hijacked(query):
    assert _org_module().answer_team_country_scope(query) is None


@pytest.mark.asyncio
async def test_nonstream_chat_short_circuits_before_wrong_notion_answer(monkeypatch):
    agent = OrchestratorAgent.__new__(OrchestratorAgent)
    agent.parse_db_prefix = lambda query: (None, query)
    monkeypatch.setattr("app.core.term_aliases._load", lambda: [])

    async def no_report(*args, **kwargs):
        return None

    async def wrong_notion(*args, **kwargs):
        return {"source": "notion", "answer": "인도네시아와 말레이시아입니다."}

    agent._handle_report = no_report
    agent._keyword_classify_ex = lambda query: ("notion", True)
    agent._handle_qdrant = wrong_notion
    agent._bg_tasks = set()

    result = await agent.route_and_execute("동남아시아2팀이 담당하는 국가가 어디지?")

    assert result["source"] == "direct"
    assert "말레이시아" in result["answer"]
    assert "싱가포르" in result["answer"]
    assert "인도네시아" not in result["answer"]


@pytest.mark.asyncio
async def test_stream_chat_short_circuits_before_wrong_notion_answer(monkeypatch):
    agent = OrchestratorAgent.__new__(OrchestratorAgent)
    agent.parse_db_prefix = lambda query: (None, query)
    monkeypatch.setattr("app.core.term_aliases._load", lambda: [])

    async def wrong_notion(*args, **kwargs):
        return {"source": "notion", "answer": "인도네시아와 말레이시아입니다."}

    agent._keyword_classify_ex = lambda query: ("notion", True)
    agent._handle_qdrant = wrong_notion

    events = [
        event
        async for event in agent.route_and_stream(
            "동남아시아2팀이 담당하는 국가가 어디지?"
        )
    ]

    assert events[0] == ("source", "direct")
    answer = "".join(data for kind, data in events if kind in {"chunk", "done"})
    assert "말레이시아" in answer
    assert "싱가포르" in answer
    assert "인도네시아" not in answer


# ── 서구권 두 팀 · 국가는 매출로 확정했다 (2026-09-02) ────────────────────────
#
# ⛔ 광고 테이블로 세면 안 된다. 노출 지역까지 잡혀 WEST_Ecomm 201개국 ·
#    WEST_MKT 83개국이 나오는데 그건 담당 범위가 아니라 노출 범위다.
#    실제로 판 곳(2026 실측)은 WEST_Ecomm 미국 91.2%·호주 8.7%, WEST_MKT 미국뿐이다.
# ⚠️ 대조군이 이 방법을 보증한다 — EAST1·EAST2 는 매출로 세도 등록값과 정확히 같다.

@pytest.mark.parametrize(
    ("query", "included", "excluded"),
    [
        (
            "서구권이커머스팀은 어느 나라 담당해?",
            ("서구권이커머스팀(WEST_Ecomm)", "미국", "호주"),
            ("멕시코", "캐나다", "201"),
        ),
        (
            "WEST_MKT 담당 국가는?",
            ("서구권마케팅팀(WEST_MKT)", "미국"),
            ("호주", "83"),
        ),
    ],
)
def test_west_team_scopes_come_from_sales_not_ad_reach(query, included, excluded):
    answer = _org_module().answer_team_country_scope(query)

    assert answer is not None
    for value in included:
        assert value in answer
    for value in excluded:
        assert value not in answer


def test_a_country_only_scope_says_why_it_is_short():
    """⚠️ 서구권마케팅팀 매출의 대부분은 국가가 안 붙는 B2B 다.

    그 사실을 빼고 "미국입니다" 만 내놓으면 절반만 참인 답이 된다.
    ⛔ 비율은 적지 않는다 — 해마다 변하고, 변하면 조용히 틀린 문장이 된다.
    """
    answer = _org_module().answer_team_country_scope("서구권마케팅팀 담당 국가 알려줘")

    assert "B2B" in answer
    assert "%" not in answer, "비율을 적으면 이 문장은 반드시 낡는다"


# ── 반대 방향 · 한 나라를 두 팀이 맡을 수 있다 ───────────────────────────────

def test_a_country_shared_by_two_teams_names_both():
    """⛔ 하나만 고르면 나머지 팀이 조용히 사라진다.

    서구권은 동남아처럼 국가로 나뉜 게 아니라 기능(마케팅 · 이커머스)으로 나뉘어
    있어서 미국을 두 팀이 함께 맡는다. 사용자 확정(2026-09-02): "답 두개하면 됨".
    """
    answer = _org_module().answer_team_country_scope("미국은 어느 팀이 담당해?")

    assert answer is not None
    assert "서구권마케팅팀(WEST_MKT)" in answer
    assert "서구권이커머스팀(WEST_Ecomm)" in answer
    assert "2개 팀" in answer
    assert "기능" in answer, "왜 둘인지 밝히지 않으면 데이터 오류로 읽힌다"


@pytest.mark.parametrize(
    ("query", "team"),
    [
        ("인도네시아는 어느 팀이 맡아?", "동남아시아1팀(EAST1)"),
        ("말레이시아 담당 팀은?", "동남아시아2팀(EAST2)"),
        ("호주 담당팀 알려줘", "서구권이커머스팀(WEST_Ecomm)"),
    ],
)
def test_a_country_owned_by_one_team_answers_exactly_that_team(query, team):
    answer = _org_module().answer_team_country_scope(query)

    assert answer is not None and team in answer
    assert "2개 팀" not in answer


@pytest.mark.parametrize(
    "query",
    [
        "미국 8월 매출 알려줘",
        "미국 어느 팀이 광고비 제일 많이 썼어?",
        "서구권이커머스팀 2026년 국가별 매출 알려줘",
        "미국 매출 보고서 만들어줘",
    ],
)
def test_data_questions_about_a_country_are_not_hijacked(query):
    """⚠️ 정적 관문이 데이터 질문을 가로채면 답이 아예 안 나온다."""
    assert _org_module().answer_team_country_scope(query) is None


def test_an_ambiguous_region_asks_back_instead_of_guessing_or_leaking():
    """⛔ `서구권`·`동남아` 는 팀이 아니라 권역이다 — 지어내지도, 흘리지도 않고 되묻는다.

    예전엔 그냥 통과시켰다. 그러면 관문을 못 넘고 **검색 경로로 새서** 무관한 Notion
    문서가 나온다 — 이 관문이 애초에 막으려던 바로 그 실패다 (EAST2 에 인도네시아를
    넣었던 오답). 사용자 지시(2026-09-02): *"서구권, 동남아 이렇게 애매하게 말하면
    되물어야지"*.

    ⚠️ 되묻되 **후보를 담당 국가까지 붙여서** 준다. "어느 팀인가요?" 만 던지면
       사용자가 팀 이름을 몰라 한 턴을 더 버린다.
    """
    org = _org_module()

    west = org.answer_team_country_scope("서구권은 어느 나라 담당해?")
    assert west is not None, "권역명을 흘려보내면 검색 경로로 샌다"
    assert "어느 팀을 말씀하시나요?" in west
    assert "서구권마케팅팀(WEST_MKT)" in west and "서구권이커머스팀(WEST_Ecomm)" in west
    assert "미국과 호주" in west, "후보에 담당 국가가 없으면 한 턴을 더 버린다"

    east = org.answer_team_country_scope("동남아는 어느 팀이 담당해?")
    assert east is not None
    assert "동남아시아1팀(EAST1)" in east and "동남아시아2팀(EAST2)" in east


@pytest.mark.parametrize(
    ("query", "word"),
    [
        ("서구권은 어느 나라 담당해?", "서구권"),
        ("동남아는 어느 팀이 담당해?", "동남아"),
        ("동남아시아 담당 국가 알려줘", "동남아시아"),
    ],
)
def test_the_clarification_names_the_word_the_user_used(query, word):
    """⚠️ `동남아` 가 `동남아시아` 안에 들어 있다 — 짧은 쪽이 먼저 맞으면
    사용자가 쓰지도 않은 낱말로 되묻게 된다."""
    answer = _org_module().answer_team_country_scope(query)

    assert answer.startswith(f"**{word}**")
    assert "은(는)" not in answer, "조사를 받침으로 고르지 않았다"


@pytest.mark.parametrize(
    "query",
    [
        "서구권이커머스팀은 어느 나라 담당해?",
        "동남아시아2팀이 담당하는 국가가 어디지?",
        "동남아1팀은 어느 나라를 담당해?",
    ],
)
def test_a_named_team_is_answered_not_asked_back(query):
    """⛔ 팀을 정확히 댔는데 되물으면 아는 것을 안 알려주는 것이다."""
    answer = _org_module().answer_team_country_scope(query)

    assert answer is not None
    assert "어느 팀을 말씀하시나요?" not in answer


@pytest.mark.parametrize(
    "query",
    ["서구권 2026년 국가별 매출 알려줘", "동남아 매출 보고서 만들어줘",
     "동남아시아 광고비 추이 보여줘"],
)
def test_region_data_questions_still_go_to_the_data_path(query):
    """⚠️ 되묻기가 데이터 질문까지 가로채면 답이 아예 안 나온다."""
    assert _org_module().answer_team_country_scope(query) is None


# ── 조사 ────────────────────────────────────────────────────────────────────

def test_particles_follow_the_final_consonant():
    """⛔ `미국와 호주`·`미국은(는)` 이 나가면 그 한 줄로 답 전체가 기계 티가 난다."""
    org = _org_module()

    scope = org.answer_team_country_scope("서구권이커머스팀 담당 국가 알려줘")
    assert "미국과 호주" in scope and "미국와" not in scope

    for query, good, bad in [
        ("미국은 어느 팀이 담당해?", "미국은 ", "미국는 "),
        ("호주 담당팀 알려줘", "호주는 ", "호주은 "),
    ]:
        answer = org.answer_team_country_scope(query)
        assert good in answer and bad not in answer
        assert "은(는)" not in answer

    east = org.answer_team_country_scope("동남아시아1팀 담당 국가 알려줘")
    assert "인도네시아와 필리핀" in east


def test_every_registered_country_resolves_back_to_a_team():
    """⚠️ 팀에 국가를 더해 놓고 반대 방향을 안 열면 절반만 동작한다."""
    org = _org_module()
    registered = {c for scope in org.VERIFIED_TEAM_COUNTRY_SCOPES.values() for c in scope}

    for country in registered:
        answer = org.answer_team_country_scope(f"{country} 담당 팀은?")
        assert answer is not None, f"{country} 를 물었는데 답이 없다"
        assert country in answer
