# -*- coding: utf-8 -*-
"""2단계 게이트 배선 — LLM 을 **언제 부르는지**가 핵심이다."""

import asyncio

import pytest

from app.agents.orchestrator import OrchestratorAgent


class FakeFlash:
    def __init__(self, answer="SELF"):
        self.answer, self.calls = answer, 0

    def generate(self, prompt, temperature=0.0):
        self.calls += 1
        self.prompt = prompt
        return self.answer


@pytest.fixture
def agent():
    return OrchestratorAgent.__new__(OrchestratorAgent)


def test_게이트가_SELF_면_소스를_쓰지_않는다(agent):
    flash = FakeFlash("SELF")
    got = asyncio.run(agent._needs_source("파이썬으로 csv 읽는 코드 짜줘", flash))
    assert got is False
    assert flash.calls == 1


def test_게이트가_NEED_면_소스를_쓴다(agent):
    flash = FakeFlash("NEED")
    assert asyncio.run(agent._needs_source("2026년 8월 매출 알려줘", flash)) is True


def test_게이트가_터지면_뒤지는_쪽이다(agent):
    """⛔ 사내 데이터 우선 — 실패는 안전한 쪽으로 떨어진다."""
    class Boom:
        def generate(self, *a, **k):
            raise RuntimeError("timeout")

    assert asyncio.run(agent._needs_source("아무 질문", Boom())) is True


def test_질문이_프롬프트에_실린다(agent):
    flash = FakeFlash("SELF")
    asyncio.run(agent._needs_source("주간 회의록 템플릿 좀 짜줘", flash))
    assert "주간 회의록 템플릿 좀 짜줘" in flash.prompt
    assert "애매하면 NEED" in flash.prompt


def test_게이트가_두_경로에_모두_걸려_있다():
    """⛔ 한쪽만 달면 스트리밍이냐에 따라 답이 갈린다."""
    src = open("app/agents/orchestrator.py", encoding="utf-8").read()
    assert src.count("_needs_source(") >= 3   # 정의 1 + 호출 2
    assert src.count("source_gate_self") == 2


def test_노션_사용법은_1단계가_확신으로_잡는다(agent):
    """⚠️ 2단계 게이트는 이 질문을 SELF 로 **틀린다**(실측). 1단계가 잡아
       LLM 에 가지 않는 것이 유일한 보호막이다 — 깨지면 여기서 먼저 걸린다."""
    route, confident = OrchestratorAgent._keyword_classify_ex(agent, "노션 사용법 알려줘")
    assert (route, confident) == ("notion", True)


# ── 배선 회귀: 문자열 카운트가 아니라 실제 route_and_execute/route_and_stream 을
#    구동해 SELF 판정이 진짜로 route="direct" 를 만드는지 본다. 리뷰 지적(Finding 3):
#    "새 else 분기를 나중에 '단순화'하며 재분류를 빼도 문자열 카운트는 그대로 통과한다."
#
#    ⚠️ `_handle_direct`/`_stream_direct_with_fallback` 자체(진짜 Claude 호출)는
#       경계에서 잘라낸다 — 이 저장소의 기존 관례와 같다
#       (`test_single_source_fast_path.py` 가 `run_sql_agent_stream` 을 자르는 것과 동일).
#       그 안쪽(진짜 LLM 스트리밍)은 이 파일이 검증할 대상이 아니다 — 여기서 보는 것은
#       "게이트가 SELF 면 그 경계까지 route/새 route 가 실제로 direct 로 도착하는가" 다.

async def _no_report(*args, **kwargs):
    return None


async def _no_wiki(*args, **kwargs):
    return ""


class _NoMaintenance:
    active = False
    manual = False


def _prepare_gate_agent(monkeypatch):
    """`_needs_source` 를 뺀 모든 조기 관문(대시보드·조직·거절·회사정보·성분·
    유통기한·재고·초상권·보고서)이 조용히 통과하도록 준비한 실제 agent.

    ⛔ 이 준비는 `_needs_source` 를 몰래 대신 판정하지 않는다 — 실제로 SELF/NEED
       를 정하는 것은 여전히 (모킹된) `flash.generate` → `parse_gate` 다.
    """
    from app.agents import orchestrator as orch_module
    from app.agents.orchestrator import OrchestratorAgent

    agent = OrchestratorAgent()
    agent.parse_db_prefix = lambda query: (None, query)
    agent._handle_report = _no_report
    monkeypatch.setattr(
        "app.core.safety.get_maintenance_manager", lambda: _NoMaintenance()
    )
    monkeypatch.setattr(
        "app.agents.skill_memory.load_skill_context", lambda *a, **k: ""
    )
    return agent, orch_module


# 조기 관문을 전부 안 건드리고 통과하는 것을 앞서 실측으로 확인한 질문
# (company_facts/dashboard_links/org_structure/rejection_kind/ingredient/expiry/
#  inventory/model_rights/wants_report 전부 None·False·"none").
_SELF_LOOKING_QUERY = "아무 질문이든 상관없다 답해줘"


def test_게이트가_SELF면_route_and_execute가_direct로_간다(monkeypatch):
    agent, orch_module = _prepare_gate_agent(monkeypatch)

    monkeypatch.setattr(orch_module, "get_flash_client", lambda: FakeFlash("SELF"))
    agent._keyword_classify_ex = lambda query: ("bigquery", False)

    calls = []

    async def fake_classify(*a, **k):
        calls.append("classify_with_llm")
        return "bigquery"

    async def fake_direct(query, messages, conversation_context, model_type,
                           user_email, **kwargs):
        calls.append("handle_direct")
        return {"source": "direct", "answer": "sentinel"}

    agent._classify_with_llm = fake_classify
    agent._handle_direct = fake_direct

    result = asyncio.run(agent.route_and_execute(_SELF_LOOKING_QUERY))

    assert result["source"] == "direct"
    assert calls == ["handle_direct"]   # _classify_with_llm 은 절대 불리지 않는다


def test_게이트가_NEED면_route_and_execute가_재분류로_간다(monkeypatch):
    """반대 방향도 함께 지킨다 — NEED 면 여전히 6지선다(`_classify_with_llm`)로 간다."""
    agent, orch_module = _prepare_gate_agent(monkeypatch)

    monkeypatch.setattr(orch_module, "get_flash_client", lambda: FakeFlash("NEED"))
    agent._keyword_classify_ex = lambda query: ("bigquery", False)

    calls = []

    async def fake_classify(*a, **k):
        calls.append("classify_with_llm")
        return "notion"

    agent._classify_with_llm = fake_classify

    async def fake_notion(*a, **k):
        calls.append("handle_qdrant")
        return {"source": "notion", "answer": "sentinel"}

    agent._handle_qdrant = fake_notion

    result = asyncio.run(agent.route_and_execute(_SELF_LOOKING_QUERY))

    assert "classify_with_llm" in calls
    assert result["source"] == "notion"


def test_게이트가_SELF면_route_and_stream이_direct를_방출한다(monkeypatch):
    agent, orch_module = _prepare_gate_agent(monkeypatch)

    monkeypatch.setattr(orch_module, "get_flash_client", lambda: FakeFlash("SELF"))
    monkeypatch.setattr(
        "app.knowledge.wiki_search.search_with_pages", _no_wiki
    )
    agent._keyword_classify_ex = lambda query: ("bigquery", False)
    agent._needs_web_search = lambda query: False
    agent._build_direct_system_prompt = lambda: "SYS"
    monkeypatch.setattr(orch_module, "get_llm_client", lambda model_type: object())

    calls = []

    async def fake_classify(*a, **k):
        calls.append("classify_with_llm")
        return "bigquery"

    agent._classify_with_llm = fake_classify

    def fake_stream_direct(*a, **k):
        yield "ok"

    monkeypatch.setattr(orch_module, "_stream_direct_with_fallback", fake_stream_direct)

    events = asyncio.run(_collect(agent.route_and_stream(_SELF_LOOKING_QUERY)))

    assert ("source", "direct") in events
    assert "classify_with_llm" not in calls
    assert any(kind == "chunk" and data == "ok" for kind, data in events)


def test_게이트가_NEED면_route_and_stream이_새_소스를_방출한다(monkeypatch):
    """반대 방향 — NEED 면 재분류된 새 소스가 그대로 방출돼야 한다."""
    agent, orch_module = _prepare_gate_agent(monkeypatch)

    monkeypatch.setattr(orch_module, "get_flash_client", lambda: FakeFlash("NEED"))
    monkeypatch.setattr(
        "app.knowledge.wiki_search.search_with_pages", _no_wiki
    )
    agent._keyword_classify_ex = lambda query: ("bigquery", False)

    calls = []

    async def fake_classify(*a, **k):
        calls.append("classify_with_llm")
        return "notion"

    agent._classify_with_llm = fake_classify

    async def fake_qdrant(*a, **k):
        calls.append("handle_qdrant")
        return {"answer": "sentinel"}

    agent._handle_qdrant = fake_qdrant

    events = asyncio.run(_collect(agent.route_and_stream(_SELF_LOOKING_QUERY)))

    assert "classify_with_llm" in calls
    assert ("source", "notion") in events


async def _collect(stream):
    return [event async for event in stream]
