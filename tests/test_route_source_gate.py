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
