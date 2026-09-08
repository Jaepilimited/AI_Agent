# -*- coding: utf-8 -*-
"""파생 사본 신선도 감시 — 2026-09-03.

⛔ **사용자 지시**: *"그 db가 계속 업데이트 되는지 확인해야하는 시스템이 있어야할것같음.
   이렇게 누군가 알아차리고 말하는것보다 니가 스스로 발전해야하는거야"*

같은 날 세 가지가 **전부 사람 눈으로** 발견됐다. 그중 CS 캐시는 기존 감시로는
**구조적으로 못 잡는다** — `EXPECTED_JOBS` 는 *잡이 돌았는가* 를 보는데 CS 는
**잡 자체가 없었다.** 없는 잡은 "빠졌다" 고 말할 수가 없다.

그래서 두 겹이다:
  A. 신선도 — 사본이 원본보다 뒤처졌는가
  B. **커버리지** — 감시를 붙이는 걸 잊은 소스가 있는가  ← 1번을 잡는 층
"""
from datetime import datetime, timedelta

import pytest


# ── A. 신선도: 나이가 아니라 뒤처짐을 본다 ──────────────────────────────────

def test_old_copy_is_fine_when_the_source_did_not_move():
    """⚠️ 나이로만 보면 아무도 안 고친 시트마다 매일 경고가 뜬다 —
    그러면 아무도 안 읽는다. 원본이 안 움직였으면 사본이 오래돼도 정상이다."""
    from app.core.data_freshness import _lag_reading

    long_ago = datetime.now() - timedelta(days=30)
    r = _lag_reading("t", ours=long_ago, source=long_ago - timedelta(hours=1),
                     max_lag_hours=2)
    assert r.ok, r.detail


def test_copy_behind_the_source_is_flagged():
    """⛔ 이것이 CS 사고의 모양이다 — 시트는 09:41 에 고쳐졌는데 사본은 전날 16:18."""
    from app.core.data_freshness import _lag_reading

    ours = datetime(2026, 9, 2, 16, 18)
    source = datetime(2026, 9, 3, 9, 41)
    r = _lag_reading("CS", ours=ours, source=source, max_lag_hours=2)
    assert not r.ok
    assert "원본이" in r.detail and "앞서 있다" in r.detail


def test_missing_copy_is_a_failure_not_a_pass():
    from app.core.data_freshness import _lag_reading

    r = _lag_reading("t", ours=None, source=datetime.now(), max_lag_hours=2)
    assert not r.ok and "한 번도" in r.detail


def test_unreadable_source_falls_back_to_age_and_says_so():
    """⚠️ 실측: OP 재고 시트는 Drive 404 라 원본 시각을 못 읽는다.
    조용히 통과시키지 말고 **나이로만 봤다는 사실을 적는다.**"""
    from app.core.data_freshness import _lag_reading

    fresh = _lag_reading("t", ours=datetime.now() - timedelta(hours=1),
                         source=None, max_lag_hours=2, max_age_hours=26)
    assert fresh.ok and "나이로만" in fresh.detail

    stale = _lag_reading("t", ours=datetime.now() - timedelta(hours=99),
                         source=None, max_lag_hours=2, max_age_hours=26)
    assert not stale.ok


def test_a_broken_probe_does_not_hide_the_others():
    """⚠️ 하나가 터졌다고 나머지 감시가 통째로 멈추면 안 된다."""
    from app.core import data_freshness as DF

    def _boom():
        raise RuntimeError("db down")

    good = DF.Source("ok", lambda: DF.Reading("ok", True, "fine"))
    bad = DF.Source("bad", _boom)
    original = DF.SOURCES[:]
    try:
        DF.SOURCES[:] = [bad, good]
        readings = DF.check()
        assert len(readings) == 2
        assert [r.ok for r in readings] == [False, True]
        assert "측정 실패" in readings[0].detail
    finally:
        DF.SOURCES[:] = original


# ── B. 커버리지: 감시를 붙이는 걸 잊은 것까지 잡는다 ────────────────────────

def test_every_user_facing_source_is_watched():
    """지금 빠진 것이 없어야 한다 — 있으면 등록하거나 이유를 적어야 한다."""
    from app.core.data_freshness import coverage_gaps

    gaps = coverage_gaps()
    assert not gaps, f"신선도 감시가 없는 소스: {gaps}"


def test_coverage_actually_fires_when_a_source_is_unwatched():
    """⛔ **불이 켜지지 않는 경보는 감시가 아니다.** 감시를 떼면 정말 걸리는가."""
    from app.core import data_freshness as DF

    original = DF.SOURCES[:]
    try:
        # CS/BP(=`cs` 라우트) 감시를 떼어 본다 — 2026-09-03 사고의 재현
        DF.SOURCES[:] = [s for s in original if "cs" not in s.routes]
        gaps = DF.coverage_gaps()
        assert any("BP" in g or "cs" in g for g in gaps), gaps
    finally:
        DF.SOURCES[:] = original


def test_non_copies_are_excluded_with_a_written_reason():
    """⚠️ 이유 없이 빼면 다음 사람이 '빠뜨렸나?' 를 매번 다시 조사한다."""
    from app.core.data_freshness import NOT_A_COPY

    assert set(NOT_A_COPY) >= {"gws", "report"}
    for route, reason in NOT_A_COPY.items():
        assert len(reason) > 8, f"{route} 에 이유가 없다"


# ── 자가 점검 배선 ──────────────────────────────────────────────────────────

def test_both_layers_run_on_the_server():
    """⛔ 서버에는 pytest 가 없다 — 자가 점검에 등록해야 매일 돈다."""
    from app.core.self_check import CHECKS

    ids = {c.id for c in CHECKS}
    assert "data_freshness" in ids
    assert "freshness_coverage" in ids


def test_coverage_gap_is_critical_not_a_warning():
    """감시가 없다는 것은 '조금 낡았다' 보다 무겁다 — 그 구멍은 무한히 조용하다."""
    from app.core.self_check import CHECKS, SEV_CRITICAL

    c = next(c for c in CHECKS if c.id == "freshness_coverage")
    assert c.severity == SEV_CRITICAL


def test_registry_covers_the_sources_this_session_fixed():
    """오늘 사람이 잡아낸 것들이 이제 자동으로 감시되는가."""
    from app.core.data_freshness import SOURCES

    names = {s.name for s in SOURCES}
    assert "CS/BP 제품 Q&A" in names      # 기동 시에만 갱신되던 것
    assert "대표 제품 목록" in names        # 하드코딩이던 것
    assert "제품 전성분" in names
