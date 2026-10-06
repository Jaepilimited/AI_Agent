# -*- coding: utf-8 -*-
"""후속 질문 이어 받기 — 재작성 + 시각화 재사용 (2026-10-06).

사용자 지시: *"컨텍스트가 항상 부족해보임. 실제 서비스처럼 질문을 잘 저장해서
이어지게 하는 시스템이 필요함"*. 프로덕션에서 실제로 끊긴 것:

- 붐따 #181: "회사 창립일 언제야?" → "정확한 일자 알려줘" 가 문서 검색으로 새서
  "어떤 항목의 일자인지 명시되지 않았다" + 무관한 업로드 일정 목록
- 같은 대화의 "시각화 해줘" 가 SQL 을 새로 만들어 총액이 2.6억 → 1.9억으로 바뀜
"""

import asyncio
import json

import pytest

from app.core import followup_rewrite as fr

HISTORY = [
    {"role": "user", "content": "회사 창립일 언제야?"},
    {"role": "assistant", "content": "**2014년 8월**입니다. (주)크레이버코퍼레이션의 설립 시점입니다."},
]


def _msgs(q, history=HISTORY):
    return history + [{"role": "user", "content": q}]


# ── 1. 고르기 ─────────────────────────────────────────────────────────────
@pytest.mark.parametrize("q", [
    "정확한 일자 알려줘",        # 붐따 #181
    "인도네시아는?",
    "수량도 같이 알려줘",
    "국가/채널 기준으로 하면?",
    "그럼 일본은?",
])
def test_후속_질문은_재작성_대상이다(q):
    assert fr.needs_rewrite(q, _msgs(q))


@pytest.mark.parametrize("q", [
    "2026년 7월 미국 스킨1004 매출을 채널별로 나눠서 전년 동월과 비교해서 보여줘 그리고 상위 5개 채널은 따로 정리해줘",
    "@@매출 일본 매출",
    "시각화 해줘",            # 재작성하지 않고 직전 SQL 을 다시 돌린다
])
def test_혼자_서는_질문과_시각화는_재작성하지_않는다(q):
    assert not fr.needs_rewrite(q, _msgs(q))


def test_첫_질문은_재작성하지_않는다():
    assert not fr.needs_rewrite("인도네시아는?", [{"role": "user", "content": "인도네시아는?"}])


def test_보고서_확인_질문에_대한_답은_원문으로_둔다():
    hist = [{"role": "user", "content": "일본 매출 보고서"},
            {"role": "assistant", "content": "두 가지만 확인할게요 <!-- report-clarification-v1:abc -->"}]
    assert not fr.needs_rewrite("2026 상반기 / 채널별", _msgs("2026 상반기 / 채널별", hist))


@pytest.mark.parametrize("q,expected", [
    ("시각화 해줘", True), ("시각화가 안보이네", True), ("차트로 보여줘", True), ("표로 보여줘", True),
    ("국가별로 시각화해줘", False), ("월별 추이 그래프", False), ("상위 10개 차트", False),
    ("인도네시아는?", False),
])
def test_시각화_요청_판정(q, expected):
    assert fr.is_presentation_followup(q) is expected


# ── 2. 재작성 + 검사 ──────────────────────────────────────────────────────
def _fake_llm(monkeypatch, standalone):
    monkeypatch.setattr(fr, "_call_llm", lambda prompt: json.dumps(
        {"standalone": standalone, "depends": True}, ensure_ascii=False))


def test_붐따181_정확한_일자가_창립일_질문으로_이어진다(monkeypatch):
    _fake_llm(monkeypatch, "크레이버코퍼레이션 회사 창립일의 정확한 일자 알려줘")
    rw = asyncio.run(fr.rewrite("정확한 일자 알려줘", _msgs("정확한 일자 알려줘")))
    assert rw.changed and "창립일" in rw.standalone
    note = fr.interpretation_note(rw)
    assert "창립일" in note and note.startswith(">"), "바꿨으면 답변 앞에 말해야 한다"


def test_없던_숫자를_지어내면_버린다(monkeypatch):
    _fake_llm(monkeypatch, "2025년 3월 회사 창립일 알려줘")   # 2025·3 은 어디에도 없다
    rw = asyncio.run(fr.rewrite("정확한 일자 알려줘", _msgs("정확한 일자 알려줘")))
    assert not rw.changed and rw.standalone == "정확한 일자 알려줘"
    assert rw.reason.startswith("invented_number")


def test_앞_대화에_있던_숫자는_쓸_수_있다(monkeypatch):
    _fake_llm(monkeypatch, "2014년 8월 창립한 회사의 정확한 창립 일자 알려줘")
    rw = asyncio.run(fr.rewrite("정확한 일자 알려줘", _msgs("정확한 일자 알려줘")))
    assert rw.changed


def test_너무_길어지면_버린다(monkeypatch):
    _fake_llm(monkeypatch, "창립일 " * 80)
    rw = asyncio.run(fr.rewrite("인도네시아는?", _msgs("인도네시아는?")))
    assert not rw.changed and rw.reason == "too_long"


def test_LLM_이_죽으면_원문으로_간다(monkeypatch):
    def boom(prompt):
        raise RuntimeError("down")
    monkeypatch.setattr(fr, "_call_llm", boom)
    rw = asyncio.run(fr.rewrite("인도네시아는?", _msgs("인도네시아는?")))
    assert not rw.changed and rw.standalone == "인도네시아는?" and rw.reason == "llm_failed"


def test_그대로_돌려주면_바꾼_것이_아니다(monkeypatch):
    _fake_llm(monkeypatch, "인도네시아는 ?")
    rw = asyncio.run(fr.rewrite("인도네시아는?", _msgs("인도네시아는?")))
    assert not rw.changed and fr.interpretation_note(rw) == ""


def test_저장된_해석을_다음_재작성에_싣는다():
    hist = [{"role": "user", "content": "2026년 7월 미국 매출"},
            {"role": "assistant", "content": "219.7억"},
            {"role": "user", "content": "인도네시아는?"},
            {"role": "assistant", "content": "88.3억"},
            {"role": "user", "content": "그럼 일본은?"}]
    stored = {fr._hash("인도네시아는?"): "2026년 7월 인도네시아 매출"}
    h = fr.build_history(hist, stored)
    assert "(해석: 2026년 7월 인도네시아 매출)" in h, "후속의 후속이 끊긴다"
    assert "그럼 일본은?" not in h, "현재 질문은 앞 대화에 넣지 않는다"


def test_답변의_SQL_표_차트는_재작성_입력에서_뺀다():
    hist = [{"role": "user", "content": "미국 매출"},
            {"role": "assistant", "content": "요약 219.7억\n| a | b |\n```chart-config\n{}\n```\n"
                                             "<details><summary>실행된 쿼리</summary>```sql\nSELECT 1\n```</details>"},
            {"role": "user", "content": "일본은?"}]
    h = fr.build_history(hist)
    assert "SELECT" not in h and "chart-config" not in h and "| a |" not in h


# ── 3. 진입점 연결 — 두 경로가 같은 규칙 ───────────────────────────────────
from app.agents import orchestrator as orch
from app.agents.orchestrator import OrchestratorAgent


class _On:
    followup_rewrite_enabled = True

    def __getattr__(self, name):
        from app.config import get_settings
        return getattr(get_settings(), name)


@pytest.fixture
def rewrite_on(monkeypatch):
    monkeypatch.setattr("app.config.get_settings", lambda: _On())

    async def fake_rewrite(q, messages, user_email=""):
        return fr.Rewrite(q, "크레이버 창립일 정확한 일자 알려줘", True, "rewritten")
    monkeypatch.setattr(fr, "rewrite", fake_rewrite)


def _agent():
    return OrchestratorAgent.__new__(OrchestratorAgent)


def test_스트리밍은_재작성한_질문으로_다시_타고_안내를_첫_출력에_한_번만_붙인다(rewrite_on):
    agent = _agent()
    seen = {}

    async def inner(q, *a, _followup_done=False, **k):
        seen["q"], seen["done"] = q, _followup_done
        yield ("source", "notion")
        yield ("chunk", "답1")
        yield ("chunk", "답2")
        yield ("done", "답1답2")

    agent.route_and_stream = inner   # 재귀 호출만 가로챈다 (바깥은 클래스 함수)

    async def run():
        return [e async for e in OrchestratorAgent.route_and_stream(
            agent, "정확한 일자 알려줘", _msgs("정확한 일자 알려줘"))]
    events = asyncio.run(run())
    assert seen == {"q": "크레이버 창립일 정확한 일자 알려줘", "done": True}
    chunks = [d for t, d in events if t == "chunk"]
    assert chunks[0].startswith("> 💬") and "답1" in chunks[0]
    assert sum("💬" in (d or "") for _, d in events) == 1, "안내가 두 번 붙었다"


def test_스트리밍_done_만_있는_경로에도_안내가_붙는다(rewrite_on):
    agent = _agent()

    async def inner(q, *a, **k):
        yield ("source", "bigquery")
        yield ("done", "표 답변")
    agent.route_and_stream = inner

    async def run():
        return [e async for e in OrchestratorAgent.route_and_stream(
            agent, "인도네시아는?", _msgs("인도네시아는?"))]
    events = asyncio.run(run())
    done = [d for t, d in events if t == "done"][0]
    assert done.startswith("> 💬") and done.endswith("표 답변")


def test_비스트리밍도_같은_규칙(rewrite_on):
    agent = _agent()
    seen = {}

    async def inner(q, *a, _followup_done=False, **k):
        seen["q"], seen["done"] = q, _followup_done
        return {"source": "notion", "answer": "답"}
    agent.route_and_execute = inner

    result = asyncio.run(OrchestratorAgent.route_and_execute(
        agent, "정확한 일자 알려줘", _msgs("정확한 일자 알려줘")))
    assert seen["done"] is True and "창립일" in seen["q"]
    assert result["answer"].startswith("> 💬") and result["answer"].endswith("답")


def test_시각화_요청은_직전_SQL_을_그대로_다시_돌린다(monkeypatch):
    monkeypatch.setattr("app.config.get_settings", lambda: _On())
    from app.agents import sql_agent as sa
    sql = "SELECT Country, SUM(Sales1_R) s FROM `skin1004-319714.Sales_Integration.SALES_ALL_Backup` GROUP BY 1"
    hist = [{"role": "user", "content": "AZULENE 크림 국가별 매출"},
            {"role": "assistant", "content": f"표\n\n<details><summary>실행된 쿼리</summary>\n\n```sql\n{sql}\n```\n</details>"}]
    ran = {}

    class BQ:
        def execute_query(self, s, timeout=0, max_rows=0):
            ran["sql"] = s
            return [{"Country": "미국", "s": 3.0}, {"Country": "일본", "s": 2.0}]

    monkeypatch.setattr(sa, "get_bigquery_client", lambda: BQ())
    monkeypatch.setattr(sa, "validate_sql", lambda s, allowed_tables=None: (True, ""))
    monkeypatch.setattr(sa, "_try_generate_chart",
                        lambda llm, q, s, p, r: "\n\n```chart-config\n{}\n```")
    monkeypatch.setattr(orch, "get_flash_client", lambda: object())

    def no_rewrite(*a, **k):
        raise AssertionError("시각화 요청을 재작성했다 — SQL 이 새로 만들어진다")
    monkeypatch.setattr(fr, "rewrite", no_rewrite)

    result = asyncio.run(OrchestratorAgent.route_and_execute(
        _agent(), "시각화 해줘", _msgs("시각화 해줘", hist)))
    assert ran["sql"] == sql, "직전 SQL 이 아닌 것을 돌렸다"
    assert "chart-config" in result["answer"] and "같은 SQL" in result["answer"]
    assert "```sql" in result["answer"], "근거 SQL 이 빠지면 다음 후속이 이어받지 못한다"


def test_직전_답변에_SQL_이_없으면_평소대로_간다(monkeypatch):
    monkeypatch.setattr("app.config.get_settings", lambda: _On())
    out = asyncio.run(_agent()._followup_prepare(
        "시각화 해줘", _msgs("시각화 해줘"), "", [], False))
    assert out == {"query": "시각화 해줘", "note": "", "answer": None}


def test_스위치를_끄면_아무것도_하지_않는다(monkeypatch):
    class Off:
        followup_rewrite_enabled = False
    monkeypatch.setattr("app.config.get_settings", lambda: Off())

    def boom(*a, **k):
        raise AssertionError("꺼져 있는데 재작성을 불렀다")
    monkeypatch.setattr(fr, "rewrite", boom)
    out = asyncio.run(_agent()._followup_prepare(
        "인도네시아는?", _msgs("인도네시아는?"), "", [], False))
    assert out["query"] == "인도네시아는?" and out["note"] == ""


def test_테스트_기본값은_꺼져_있다():
    """기존 라우팅 테스트가 실제 Flash·BigQuery 를 부르지 않게 한다 (conftest)."""
    from app.config import get_settings
    assert get_settings().followup_rewrite_enabled is False


def test_direct_이력에는_내부_테이블_태그가_없다():
    """direct 모델이 태그를 흉내 내 답변 첫머리에 내부 테이블 경로를 쓰던 것 (2026-09 실측)."""
    from app.agents.orchestrator import _clean_messages_for_history
    sql = "SELECT 1 FROM `skin1004-319714.Sales_Integration.SALES_ALL_Backup`"
    msgs = [{"role": "user", "content": "매출"},
            {"role": "assistant", "content": (
                "219.7억\n\n<details><summary>실행된 쿼리</summary>\n\n"
                f"```sql\n{sql}\n```\n</details>")},
            {"role": "user", "content": "고마워"}]
    out = _clean_messages_for_history(msgs)
    assert "실행된 쿼리 테이블" not in out[1]["content"]
    assert "219.7억" in out[1]["content"]


def test_독립_질문의_첫머리_접속어는_뗀다(monkeypatch):
    """프로덕션 실측(2026-10-06): 「그럼 2026년 9월 일본 매출 얼마야?」 로 안내됐다."""
    _fake_llm(monkeypatch, "그럼 회사 창립일의 정확한 일자 알려줘")
    rw = asyncio.run(fr.rewrite("정확한 일자 알려줘", _msgs("정확한 일자 알려줘")))
    assert rw.changed and rw.standalone == "회사 창립일의 정확한 일자 알려줘"
