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
    # ⛔ SELF 만 세면 **분자만 있고 분모가 없다** — SELF 가 두 배로 는 것과
    #    트래픽이 두 배가 된 것을 구분할 수 없다. NEED 도 같은 자리에서 센다.
    assert src.count("source_gate_need") == 2


def test_게이트가_오래_걸리면_상한에서_끊고_뒤지는_쪽으로_간다(agent):
    """⛔ 상한이 없으면 `_gemini_retry` 4회(0.5·1.5·4.0초 백오프)를 다 태우고,
       NEED 면 `_classify_with_llm` 이 **같은 예산을 한 번 더** 태운다.
       둘 다 사용자가 첫 글자를 보기 전이다. 실측 중앙값은 1.38초다.
    """
    import time

    from app.agents import orchestrator as orch_module

    class Slow:
        def generate(self, *a, **k):
            time.sleep(1.0)
            return "SELF"          # 제때 왔다면 SELF 였을 것

    async def _run():
        # ⚠️ 시간은 **코루틴 안에서** 잰다. `asyncio.run` 은 나갈 때 기본
        #    executor 를 기다리므로(그 안에서 스레드가 sleep 을 마저 끝낸다)
        #    바깥에서 재면 상한이 들었는데도 1초가 나온다.
        started = time.monotonic()
        got = await agent._needs_source("아무 질문", Slow())
        return got, time.monotonic() - started

    orig = orch_module._SOURCE_GATE_TIMEOUT_SECONDS
    try:
        orch_module._SOURCE_GATE_TIMEOUT_SECONDS = 0.05
        got, waited = asyncio.run(_run())
    finally:
        orch_module._SOURCE_GATE_TIMEOUT_SECONDS = orig

    assert got is True        # 시간이 다하면 안전한 쪽 = 뒤진다
    assert waited < 0.9       # 실제로 기다리지 않고 끊었다


def test_상한이_실측_중앙값보다_넉넉하다():
    """⚠️ 상한을 중앙값(1.38초) 가까이 조이면 멀쩡한 판정이 잘려 나간다."""
    from app.agents.orchestrator import _SOURCE_GATE_TIMEOUT_SECONDS

    assert 3.0 <= _SOURCE_GATE_TIMEOUT_SECONDS <= 10.0


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


# ── Task 7: SELF 로 답했으면 「사내 데이터로 확인해 드릴까요?」 한 줄을 붙인다 ──

def test_사내데이터로_확인해_드릴까요_한_줄():
    from app.core.route_intent import source_free_footer

    footer = source_free_footer()
    assert "사내 데이터" in footer
    assert footer.startswith("\n")          # 본문과 붙지 않는다
    # ⛔ 지키지 못할 약속을 하지 않는다 — 원 질문을 다음 턴으로 나르는 코드가 없다
    assert "조회해 드립니다" not in footer


def test_돌아갈_문이_평문이_아니라_후속_칩이_된다():
    """⛔ `chat.js` 가 칩으로 만드는 것은 **머리말이 걸릴 때뿐**이다.

    처음 문안("💡 사내 데이터로 확인해 드릴까요?")은 그 정규식 어디에도 안 걸려
    답변 **맨 끝의 평문**으로 렌더됐다 — 이 저장소가 두 번 "거기는 아무도 안
    읽는다" 고 결론 낸 자리다. 그래서 정규식을 `chat.js` 에서 **직접 읽어** 맞춘다
    (여기 손으로 베껴 적으면 프론트가 바뀔 때 조용히 어긋난다).
    """
    import re

    from app.core.route_intent import source_free_footer

    js = open("app/frontend/chat.js", encoding="utf-8").read()
    m = re.search(r"if \(/([^/]+)/i\.test\(ltrim\)\)", js)
    assert m, "chat.js 의 후속 질문 머리말 판정 정규식을 찾지 못했다"
    header_re = re.compile(m.group(1), re.IGNORECASE)

    lines = [ln.strip() for ln in source_free_footer().split("\n") if ln.strip()]
    header = lines[0]
    assert "💡" in header                       # 💡 없는 줄은 아예 후보가 아니다
    assert header_re.search(header), (header, m.group(1))

    # 머리말 다음 줄이 실제로 칩 문구로 뽑히는 모양이어야 한다
    item = re.match(r"^>?\s*[-*]\s*(.+?)\s*$", lines[1])
    assert item, lines[1]
    assert 5 < len(item.group(1)) < 120


def test_한_줄이_두_경로에_모두_걸려_있다():
    """⛔ 한쪽만 달면 스트리밍이냐에 따라 답이 갈린다."""
    src = open("app/agents/orchestrator.py", encoding="utf-8").read()
    assert src.count("source_free_footer()") == 2


def test_게이트가_SELF면_route_and_execute_답변에_돌아갈_문이_붙는다(monkeypatch):
    """게이트가 SELF 였다는 사실이 실제로 footer 로 전달되는지 실행해서 본다."""
    agent, orch_module = _prepare_gate_agent(monkeypatch)

    monkeypatch.setattr(orch_module, "get_flash_client", lambda: FakeFlash("SELF"))
    agent._keyword_classify_ex = lambda query: ("bigquery", False)

    async def fake_direct(query, messages, conversation_context, model_type,
                           user_email, **kwargs):
        return {"source": "direct", "answer": "sentinel"}

    agent._classify_with_llm = None  # 불리면 안 된다 — 안 부르므로 그대로 둠
    agent._handle_direct = fake_direct

    result = asyncio.run(agent.route_and_execute(_SELF_LOOKING_QUERY))

    assert result["answer"].startswith("sentinel")
    assert "사내 데이터" in result["answer"]


def test_게이트가_NEED면_route_and_execute_답변에_돌아갈_문이_안_붙는다(monkeypatch):
    """반대 방향 — 정말로 소스를 뒤졌으면 되돌아갈 문을 붙이지 않는다."""
    agent, orch_module = _prepare_gate_agent(monkeypatch)

    monkeypatch.setattr(orch_module, "get_flash_client", lambda: FakeFlash("NEED"))
    agent._keyword_classify_ex = lambda query: ("bigquery", False)

    async def fake_classify(*a, **k):
        return "notion"

    agent._classify_with_llm = fake_classify

    async def fake_notion(*a, **k):
        return {"source": "notion", "answer": "sentinel"}

    agent._handle_qdrant = fake_notion

    result = asyncio.run(agent.route_and_execute(_SELF_LOOKING_QUERY))

    assert result["answer"] == "sentinel"
    assert "사내 데이터" not in result["answer"]


def test_게이트가_SELF면_route_and_stream_이_돌아갈_문_청크를_방출한다(monkeypatch):
    agent, orch_module = _prepare_gate_agent(monkeypatch)

    monkeypatch.setattr(orch_module, "get_flash_client", lambda: FakeFlash("SELF"))
    monkeypatch.setattr(
        "app.knowledge.wiki_search.search_with_pages", _no_wiki
    )
    agent._keyword_classify_ex = lambda query: ("bigquery", False)
    agent._needs_web_search = lambda query: False
    agent._build_direct_system_prompt = lambda: "SYS"
    monkeypatch.setattr(orch_module, "get_llm_client", lambda model_type: object())

    def fake_stream_direct(*a, **k):
        yield "ok"

    monkeypatch.setattr(orch_module, "_stream_direct_with_fallback", fake_stream_direct)

    events = asyncio.run(_collect(agent.route_and_stream(_SELF_LOOKING_QUERY)))

    chunks = [data for kind, data in events if kind == "chunk"]
    assert any("사내 데이터" in c for c in chunks)


# ── F2: 사고 때의 레버 — 코드 수정·재배포 없이 끌 수 있어야 한다 (설계 §6) ──
#    ⛔ 꺼졌을 때의 동작은 **도입 이전과 정확히 같다**: 곧바로 6지선다로 간다.

def _disable_source_gate(monkeypatch):
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), "source_gate_enabled", False)


def test_게이트를_끄면_route_and_execute가_바로_재분류로_간다(monkeypatch):
    agent, orch_module = _prepare_gate_agent(monkeypatch)
    _disable_source_gate(monkeypatch)

    flash = FakeFlash("SELF")      # 켜져 있었다면 SELF 로 direct 가 됐을 것
    monkeypatch.setattr(orch_module, "get_flash_client", lambda: flash)
    agent._keyword_classify_ex = lambda query: ("bigquery", False)

    async def fake_classify(*a, **k):
        return "notion"

    agent._classify_with_llm = fake_classify

    async def fake_notion(*a, **k):
        return {"source": "notion", "answer": "sentinel"}

    agent._handle_qdrant = fake_notion

    result = asyncio.run(agent.route_and_execute(_SELF_LOOKING_QUERY))

    assert result["source"] == "notion"
    assert flash.calls == 0                 # 게이트 LLM 호출 자체가 없다
    assert "사내 데이터" not in result["answer"]   # 돌아갈 문도 안 붙는다


def test_게이트를_끄면_route_and_stream도_바로_재분류로_간다(monkeypatch):
    """⛔ 한쪽만 끄면 그것도 경로에 따라 답이 갈린다."""
    agent, orch_module = _prepare_gate_agent(monkeypatch)
    _disable_source_gate(monkeypatch)

    flash = FakeFlash("SELF")
    monkeypatch.setattr(orch_module, "get_flash_client", lambda: flash)
    monkeypatch.setattr("app.knowledge.wiki_search.search_with_pages", _no_wiki)
    agent._keyword_classify_ex = lambda query: ("bigquery", False)

    async def fake_classify(*a, **k):
        return "notion"

    agent._classify_with_llm = fake_classify

    async def fake_qdrant(*a, **k):
        return {"answer": "sentinel"}

    agent._handle_qdrant = fake_qdrant

    events = asyncio.run(_collect(agent.route_and_stream(_SELF_LOOKING_QUERY)))

    assert ("source", "notion") in events
    assert flash.calls == 0


def test_끄는_설정이_두_경로에_모두_걸려_있다():
    src = open("app/agents/orchestrator.py", encoding="utf-8").read()
    assert src.count("source_gate_enabled") == 2
    assert src.count("bare_rejection_clarify_enabled") == 2


# ── F6: 👍 few-shot 예시를 두 경로가 **같은 시점**에 읽는다 ────────────────
#    스트리밍이 재분류 **전에** 읽고 있었다. 게이트가 direct 로 뒤집은 요청은
#    비스트리밍에서만 예시를 받았고, 프로덕션은 스트리밍이라 늘 지는 쪽이었다.

def test_게이트가_direct로_뒤집어도_스트리밍이_few_shot_을_읽는다(monkeypatch):
    agent, orch_module = _prepare_gate_agent(monkeypatch)

    monkeypatch.setattr(orch_module, "get_flash_client", lambda: FakeFlash("SELF"))
    monkeypatch.setattr("app.knowledge.wiki_search.search_with_pages", _no_wiki)
    monkeypatch.setattr(
        "app.agents.skill_memory.load_skill_context",
        lambda route, query: "SKILL_EXAMPLES" if route == "direct" else "",
    )
    agent._keyword_classify_ex = lambda query: ("bigquery", False)   # 처음엔 direct 가 아니다
    agent._needs_web_search = lambda query: False
    agent._build_direct_system_prompt = lambda: "SYS"
    monkeypatch.setattr(orch_module, "get_llm_client", lambda model_type: object())

    seen = {}

    def fake_stream_direct(llm, query, messages=None, system_instruction=None, **k):
        seen["system"] = system_instruction
        yield "ok"

    monkeypatch.setattr(orch_module, "_stream_direct_with_fallback", fake_stream_direct)

    asyncio.run(_collect(agent.route_and_stream(_SELF_LOOKING_QUERY)))

    blocks = [b["text"] for b in seen["system"]]
    assert any("SKILL_EXAMPLES" in b for b in blocks), blocks


def test_few_shot_은_재분류가_끝난_뒤에_읽는다():
    """⛔ 자리가 곧 계약이다 — 앞으로 옮기면 위 테스트가 아니라 이 테스트가 먼저 말한다."""
    src = open("app/agents/orchestrator.py", encoding="utf-8").read()
    stream_at = src.index("_stream_skill_ctx = await asyncio.to_thread")
    gate_at = src.index('logger.warning("source_gate_self", path="route_and_stream"')
    assert gate_at < stream_at, "스트리밍 few-shot 로드가 재분류보다 앞에 있다"
