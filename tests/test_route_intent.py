# -*- coding: utf-8 -*-
"""직전 답변을 부정하는 발화 판정 — 실사용 60일 표본으로 고정.

⛔ 문장 **중간**의 `말고` 는 부정이 아니다. 실측 26건이 전부 정상적인 좁혀
   묻기였다("상위 10개 말고 전체로 줘"). 여기 걸리면 멀쩡한 대화가 끊긴다.
"""

import pytest

from app.core.route_intent import rejection_kind


# 실사용에서 그대로 가져온 것들 (2026-09-07, 첫머리 부정 20건 중)
INFORMATIVE = [
    "아니 ;; 내 개인 업무 노션 ;;",
    "아니 ㅠㅠㅠㅠㅠㅠ!!!!! 나 개인 업무 노션 페이지 구성할건데 제안해달라고!!!!",
    "아니 톤브라이트닝 미스트 월별 매출액 및 판매량",
    "아니 라인이 랩인네이처인 제품들 제품별 성장성 매트릭스 보여줘",
    "아니 ㅡㅡ 주간보고를 작성해달라고",
    "아니 너가 되게 해보라고 !!!!!!!!!!",
    "뭔 소리야 틀렸음; 환각을 정답처럼 말하지 마셈",
    "뭔 소리하는거야. 리센느 멤버 5명을 내부 데이터베이스에서 가져왔다고?",
]

BARE = [
    "아니...",
    "아니 ;;",
]

# 부정이 아니다 — 정상적인 좁혀 묻기
NONE = [
    "상위 10개 말고 25년도 출시된 품목 전체로 줘",
    "막대표 말고 각각 국가별 비중값 볼 수 있게 원형 그래프로 줄 수 있어?",
    "오 좋아 근데 분리만 다시해보자. 앰플, 크림, 토너 각각 따로",
    "기타국가는 뭐야? 누적 매출 비중이 가장 큰 개별 국가부터 보여주고",
    "2026년 8월 일본 매출 알려줘",
    "센텔라 앰플 특징 알려줘",
    "",
]


@pytest.mark.parametrize("text", INFORMATIVE)
def test_부정하면서_고쳐_말한_것은_informative(text):
    assert rejection_kind(text) == "informative", text


@pytest.mark.parametrize("text", BARE)
def test_부정만_하고_정보가_없으면_bare(text):
    assert rejection_kind(text) == "bare", text


@pytest.mark.parametrize("text", NONE)
def test_좁혀_묻기는_부정이_아니다(text):
    assert rejection_kind(text) == "none", text


from app.core.route_intent import SOURCE_GATE_PROMPT, parse_gate


@pytest.mark.parametrize("raw,want", [
    ("SELF", "SELF"),
    ("self", "SELF"),
    ("  SELF  ", "SELF"),
    ("SELF — 만들어 달라는 요청입니다", "SELF"),
    ("NEED", "NEED"),
    ("need", "NEED"),
])
def test_판정_문자열을_읽는다(raw, want):
    assert parse_gate(raw) == want


@pytest.mark.parametrize("raw", ["", None, "   ", "글쎄요", "NEED 또는 SELF"])
def test_모르면_뒤지는_쪽이다(raw):
    """⛔ 사내 데이터 우선 — 읽을 수 없으면 NEED 다. 안전한 쪽 실패."""
    assert parse_gate(raw) == "NEED"


def test_프롬프트가_사내데이터_우선을_못박는다():
    """이 줄이 「애매하면 뒤진다」를 보증하는 유일한 자리다."""
    assert "애매하면 NEED" in SOURCE_GATE_PROMPT
    assert "SELF" in SOURCE_GATE_PROMPT and "NEED" in SOURCE_GATE_PROMPT


def test_경계_4자까지는_정보가_없는_것으로_본다():
    """스펙이 「4자 이하」로 못박은 계약 — 경계가 움직이면 되묻기 범위가 달라진다.

    ⚠️ 실사용 표본이 아니라 **명시된 임계값**을 고정하는 테스트다.
    """
    assert rejection_kind("아니 가나다라") == "bare"        # 남는 글자 4
    assert rejection_kind("아니 가나다라마") == "informative"  # 남는 글자 5


from app.core.route_intent import clarify_message


@pytest.mark.parametrize("route", ["bigquery", "notion", "cs", "gws", "multi", "direct"])
def test_되묻는_문장은_직전에_무엇을_했는지_주장하지_않는다(route):
    """⛔ 직전 턴이 무엇을 했는지 **말하면 안 된다.**

    근거인 `_previous_route()` 는 최근 10턴 아무 assistant 메시지의 표식을 본다.
    T1 매출조회 → T2 direct 답변 → T3 "아니 ;;" 이면 아무것도 조회하지 않았는데
    "사내 데이터에서 찾아 답했습니다" 가 나간다. direct 답변이 "내부 데이터베이스"
    를 지어낸 경우에는 그 환각을 코드가 보증하는 문장으로 확인해 주기까지 한다.
    """
    msg = clarify_message(route)
    assert msg                                   # 되묻기는 계속 뜬다
    assert "?" in msg or "까요" in msg
    for claim in ("방금", "찾아 답했", "답했습니다", "조회했"):
        assert claim not in msg, (claim, msg)


def test_되묻는_문장은_고를_것을_준다():
    """앞말을 버린 대신 후보를 준다 — 아니면 무엇을 고쳐 말할지 알 수 없다."""
    msg = clarify_message("notion")
    for label in ("사내 데이터", "사내 문서", "제품 Q&A", "메일"):
        assert label in msg, label


def test_직전_경로를_모르면_되묻지_않는다():
    """고칠 대상이 없다 — 그냥 정상 라우팅한다. (호출부가 이 `""` 로 판정한다)"""
    assert clarify_message("") == ""
    assert clarify_message(None) == ""
    assert clarify_message("없는경로") == ""


def test_되묻기가_두_경로에_모두_걸려_있다():
    """⛔ 한쪽만 달면 스트리밍이냐에 따라 답이 갈린다 (이 저장소의 단골 사고)."""
    src = open("app/agents/orchestrator.py", encoding="utf-8").read()
    assert src.count("clarify_asked_bare_rejection") == 2, "두 경로에 걸려야 한다"
    assert 'path="route_and_execute"' in src
    assert 'path="route_and_stream"' in src


# ── 배선 회귀: 문자열 카운트는 **자리**를 지키지 못한다 ────────────────────
#    되묻기 블록을 `_inherit_route_for_followup` 아래로 옮기거나 `if` 하나 더
#    안쪽에 넣어도 위 카운트는 그대로 통과한다. 그래서 게이트가 이미 받은 것과
#    같은 처방을 한다 — 실제 `route_and_execute`/`route_and_stream` 을 구동해
#    되묻기 문장이 **정말로 돌아오는지** 본다.
#    ⚠️ 모킹 관례는 `tests/test_route_source_gate.py` 를 그대로 따른다.

import asyncio  # noqa: E402

from tests.test_route_source_gate import (  # noqa: E402
    FakeFlash, _collect, _no_wiki, _prepare_gate_agent,
)

_BARE_REJECTION = "아니 ;;"
_INFORMATIVE_REJECTION = "아니 ;; 내 개인 업무 노션 ;;"


def _msgs(query):
    """`messages` 마지막 원소는 **이번 질문**이다 (`_build_conversation_context`
    가 `messages[:-1]` 을 맥락으로 쓴다) — 빠뜨리면 직전 AI 답변이 통째로 잘려
    맥락이 비고, 되묻기가 조용히 안 뜬다.

    직전 AI 답변에 notion 표지를 남긴다 (`orchestrator._ROUTE_MARKERS`).
    """
    return [
        {"role": "user", "content": "신규 입사자 교안 어디 있어?"},
        {"role": "assistant",
         "content": "Notion 사내 문서 검색 결과입니다. 관련 문서를 찾았습니다."},
        {"role": "user", "content": query},
    ]


def test_되묻기가_route_and_execute에서_실제로_돌아온다(monkeypatch):
    agent, orch_module = _prepare_gate_agent(monkeypatch)
    monkeypatch.setattr(orch_module, "get_flash_client", lambda: FakeFlash("SELF"))

    called = []

    async def fake_direct(*a, **k):
        called.append("handle_direct")
        return {"source": "direct", "answer": "sentinel"}

    agent._handle_direct = fake_direct

    result = asyncio.run(
        agent.route_and_execute(_BARE_REJECTION, messages=_msgs(_BARE_REJECTION)))

    assert result["source"] == "direct"
    assert result["answer"] == clarify_message("notion")
    assert called == []          # 되묻기는 **답변을 만들기 전에** 끝난다


def test_되묻기가_route_and_stream에서_실제로_돌아온다(monkeypatch):
    agent, orch_module = _prepare_gate_agent(monkeypatch)
    monkeypatch.setattr(orch_module, "get_flash_client", lambda: FakeFlash("SELF"))
    monkeypatch.setattr("app.knowledge.wiki_search.search_with_pages", _no_wiki)

    events = asyncio.run(_collect(
        agent.route_and_stream(_BARE_REJECTION, messages=_msgs(_BARE_REJECTION))))

    assert ("source", "direct") in events
    assert ("done", clarify_message("notion")) in events


def test_고쳐_말한_것이_있으면_되묻지_않는다(monkeypatch):
    """반대 방향 — `informative` 는 그 문장으로 **정상 라우팅**된다."""
    agent, orch_module = _prepare_gate_agent(monkeypatch)
    monkeypatch.setattr(orch_module, "get_flash_client", lambda: FakeFlash("SELF"))

    async def fake_direct(*a, **k):
        return {"source": "direct", "answer": "sentinel"}

    agent._handle_direct = fake_direct

    result = asyncio.run(agent.route_and_execute(
        _INFORMATIVE_REJECTION, messages=_msgs(_INFORMATIVE_REJECTION)))

    assert result["answer"].startswith("sentinel")


def test_되묻기를_끄면_평소처럼_라우팅한다(monkeypatch):
    """⛔ 사고 때 코드 수정 없이 끌 수 있어야 한다 (설계 §6, 2단계와 독립)."""
    agent, orch_module = _prepare_gate_agent(monkeypatch)
    monkeypatch.setattr(orch_module, "get_flash_client", lambda: FakeFlash("SELF"))

    from app.config import get_settings
    monkeypatch.setattr(
        get_settings(), "bare_rejection_clarify_enabled", False)

    async def fake_direct(*a, **k):
        return {"source": "direct", "answer": "sentinel"}

    agent._handle_direct = fake_direct

    result = asyncio.run(
        agent.route_and_execute(_BARE_REJECTION, messages=_msgs(_BARE_REJECTION)))

    assert result["answer"].startswith("sentinel")
    assert clarify_message("notion") not in result["answer"]
