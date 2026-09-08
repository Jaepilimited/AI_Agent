# -*- coding: utf-8 -*-
"""CS/BP 제품 Q&A 캐시는 **매시 갱신된다** — 2026-09-03.

⛔ **사용자 제보**: *"CS DB와 제품정보에 대한 dB가 최신정보가 아닌거같은데"*

원인은 한 줄이었다. `cs_agent.warmup()` 이 **서버 기동 시에만** 불렸고,
스케줄 잡도 TTL 도 없었다 — CS 시트를 고쳐도 **재기동 전까지 반영되지 않는다.**

    프로덕션 실측: 시트 최종수정 2026-09-03 09:41 KST
                  그 직전 재기동  2026-09-02 16:18 KST   ← 17시간 동안 안 보임

⚠️ 에러가 아니다. **옛 답을 자신 있게** 내놓는 형태라 아무도 못 알아챈다
   (이 프로젝트의 기본 결함 유형이다).

⚠️ 같이 확인한 것 — 이쪽은 **정상이었다**:
   `qdrant_pipeline_daily`(CS 노션 문서 05:00) · `ingredient_sync_daily`(전성분 04:00)
   · `team_sync_daily`(01:00) 모두 3일 연속 성공. 전성분은 시트 84행 = 적재 84행.
"""
import asyncio
import inspect
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def test_hourly_refresh_job_is_scheduled():
    """⛔ 잡이 없으면 캐시는 재기동 때까지 낡는다 — 그게 원래 사고다."""
    main = _read("app/main.py")
    assert 'id="cs_cache_hourly"' in main
    assert "_cs_cache_job" in main
    # 매시여야 한다 — hour 를 박으면 하루 몇 번으로 줄어든다
    line = next(l for l in main.splitlines() if 'id="cs_cache_hourly"' in l)
    assert "hour=" not in line, "매시 갱신이어야 한다 (hour 고정 금지)"


def test_job_is_watched_by_self_check():
    from app.core.self_check import EXPECTED_JOBS

    assert "cs_cache_hourly" in EXPECTED_JOBS
    hours, label = EXPECTED_JOBS["cs_cache_hourly"]
    assert hours <= 6, "매시 잡을 하루치 여유로 두면 멈춰도 안 잡힌다"


def test_cache_state_itself_is_checked():
    """⛔ 잡이 돌았다는 것과 캐시가 채워져 있다는 것은 다르다."""
    from app.core.self_check import CHECKS

    ids = {c.id for c in CHECKS}
    assert "cs_cache" in ids


def test_refresh_keeps_the_old_cache_when_the_sheet_fails(monkeypatch):
    """⛔ 실패했다고 빈 목록으로 바꿔 끼우면 *"CS 데이터베이스가 비어있습니다"* 가 나간다.
    옛 자료라도 있는 편이 낫다."""
    from app.agents import cs_agent

    kept = [{"question": "q", "answer": "a", "line": "", "product": "",
             "brand": "", "category": ""}]
    monkeypatch.setattr(cs_agent, "_qa_cache", list(kept))
    monkeypatch.setattr(cs_agent, "_cache_loaded", True)

    async def _boom():
        raise RuntimeError("sheets down")

    monkeypatch.setattr(cs_agent, "load_all_sheets", _boom)
    assert asyncio.run(cs_agent.refresh()) == -1
    assert cs_agent._qa_cache == kept, "실패했다고 옛 캐시를 버리면 안 된다"
    assert cs_agent._cache_loaded is True, "다음 사용자 질문이 재로딩을 떠안으면 안 된다"


def test_refresh_treats_an_empty_sheet_as_failure(monkeypatch):
    """⚠️ 0건은 성공이 아니다 — 권한이 끊기거나 탭 이름이 바뀌면 이렇게 온다."""
    from app.agents import cs_agent

    kept = [{"question": "q", "answer": "a", "line": "", "product": "",
             "brand": "", "category": ""}]
    monkeypatch.setattr(cs_agent, "_qa_cache", list(kept))

    async def _empty():
        return []

    monkeypatch.setattr(cs_agent, "load_all_sheets", _empty)
    assert asyncio.run(cs_agent.refresh()) == -1
    assert cs_agent._qa_cache == kept


def test_refresh_swaps_in_new_rows_and_stamps_the_time(monkeypatch):
    from app.agents import cs_agent

    monkeypatch.setattr(cs_agent, "_qa_cache", [])
    monkeypatch.setattr(cs_agent, "_loaded_at", None)
    rows = [{"question": f"q{i}", "answer": "a", "line": "", "product": "",
             "brand": "", "category": ""} for i in range(3)]

    async def _ok():
        return rows

    monkeypatch.setattr(cs_agent, "load_all_sheets", _ok)
    assert asyncio.run(cs_agent.refresh()) == 3
    st = cs_agent.status()
    assert st["loaded"] and st["count"] == 3
    assert st["age_seconds"] is not None, "적재 시각이 없으면 낡음을 판정할 수 없다"


def test_a_failed_refresh_is_recorded_as_a_failed_job():
    """⚠️ `refresh()` 는 실패해도 옛 캐시를 지키고 -1 을 준다.
    그것을 성공으로 기록하면 자가 점검이 영영 못 잡는다."""
    main = _read("app/main.py")
    body = main[main.index("async def _cs_cache_job"):main.index("async def _ingredient_sync_job")]
    assert "if n < 0:" in body and "raise" in body


def test_startup_warmup_is_still_there():
    """⚠️ 매시 갱신을 넣었다고 기동 warmup 을 빼면 안 된다 —
    기동 직후 첫 질문이 :40 까지 빈 캐시를 본다."""
    main = _read("app/main.py")
    assert "_warmup_cs_db" in main
    src = inspect.getsource(__import__("app.agents.cs_agent", fromlist=["x"]).warmup)
    assert "load_all_sheets" in src
