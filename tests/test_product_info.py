# -*- coding: utf-8 -*-
"""제품정보(제품 스펙)는 제품 Q&A 와 **별개 소스**다 — 2026-09-04.

⛔ **사용자 지시**: *"제품 q&A는 따로이고, 제품정보는 cs데이터에 들어가야함"*

    제품 Q&A  = `[BD_BP] CS제품문의_모음집` 시트 — 문의 대응 기록 (914건)
    제품정보  = 노션 `스킨1004 전제품 한 눈에 파악하기` — 제품 스펙 (55종)

⛔ **내용은 4단 깊이에 있다.** 얕게 긁으면 제품명 목록만 나오고, 실제로 그래서
   "히알루테카 제품 정보" 질문에 경로와 링크만 답한 사고가 있었다.
"""
import pytest


def test_product_info_is_a_separate_source_from_qa():
    """⛔ 한 덩어리로 섞으면 답변이 어느 쪽을 근거로 말하는지 사라진다."""
    from app.agents import cs_agent

    src = cs_agent._build_answer_prompt("히알루테카 성분", "…Q&A…", 3)
    assert "CS 데이터베이스 검색 결과" in src
    assert "제품정보" in src
    assert "두 자료는 성격이 다릅니다" in src


def test_prompt_forbids_inventing_numbers():
    """⚠️ 함량·PPM 은 지어내기 쉬운 값이다."""
    from app.agents import cs_agent

    src = cs_agent._build_answer_prompt("성분", "ctx", 1)
    assert "지어내지 마세요" in src


def test_block_is_empty_when_nothing_matches(monkeypatch):
    from app.core import product_info as PI
    from app.agents import cs_agent

    monkeypatch.setattr(PI, "search", lambda q, limit=4: [])
    assert cs_agent._product_info_block("아무거나") == ""


def test_block_failure_does_not_kill_the_cs_answer(monkeypatch):
    """⚠️ 제품정보는 부수 자료다 — 실패해도 CS 답변은 나가야 한다."""
    from app.core import product_info as PI
    from app.agents import cs_agent

    def _boom(q, limit=4):
        raise RuntimeError("db down")

    monkeypatch.setattr(PI, "search", _boom)
    assert cs_agent._product_info_block("히알루테카") == ""


def test_sync_never_wipes_on_empty(monkeypatch):
    """⛔ 0건으로 덮으면 제품정보가 조용히 사라진다 (OP 재고·CS 캐시와 같은 규칙)."""
    from app.core import product_info as PI

    monkeypatch.setattr(PI, "collect", lambda: [])
    monkeypatch.setattr(PI, "ensure_table", lambda: None)
    stats = PI.sync()
    assert stats["written"] == 0 and stats.get("kept_old") is True


def test_status_query_avoids_reserved_words():
    """⛔ `lines` 는 MariaDB 예약어다 — 별칭으로 쓰면 1064 가 나고 `except` 가
    삼켜 **count 0** 으로 보인다 (조용한 오작동, 실측)."""
    import inspect

    from app.core import product_info as PI

    src = inspect.getsource(PI.status)
    assert " lines FROM" not in src
    assert "line_count" in src


def test_job_and_watch_are_registered():
    from app.core.self_check import EXPECTED_JOBS
    from app.core.data_freshness import SOURCES

    assert "product_info_sync_daily" in EXPECTED_JOBS
    assert any(s.name.startswith("제품정보") for s in SOURCES)


def test_zero_rows_is_treated_as_job_failure():
    """⚠️ 0건을 성공으로 기록하면 자가 점검이 영영 못 잡는다."""
    from pathlib import Path

    main = (Path(__file__).resolve().parent.parent / "app" / "main.py").read_text(encoding="utf-8")
    body = main[main.index("async def _product_info_sync_job"):
                main.index("async def _ingredient_sync_job")]
    assert 'if not stats.get("written")' in body and "raise" in body


def test_freshness_coverage_still_complete():
    from app.core.data_freshness import coverage_gaps

    assert not coverage_gaps()
