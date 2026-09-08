# -*- coding: utf-8 -*-
"""만족도 설문(별점) 회귀 — **팝업이 안 뜨는 것은 에러가 아니라 침묵이다.**

이 기능의 실패는 전부 조용하다: 임계를 잘못 세면 아무도 못 받고, 중복을 못 막으면
같은 사람에게 매번 뜨고, 처리 기준이 틀리면 개선 재료가 대기열에 안 올라온다.
셋 다 예외를 던지지 않으므로 여기서 규칙으로 고정한다.
"""
from __future__ import annotations

import pytest

from app.core import satisfaction


# ── 임계 선택 (순수 함수) ──────────────────────────────────────────────────

def test_milestones_start_at_ten_then_every_twenty_days():
    """10일차부터 20일 간격 (2026-09-02 사용자 지시: 10·30·50·70 …).

    ⛔ 처음엔 10·50·100 이었는데 간격이 벌어질수록 오래 쓴 사람이 몇 달을 조용히
       지나간다. 지금은 끝이 없다 — 계속 쓰는 사람에게 계속 묻는다.
    """
    assert satisfaction.MILESTONES[:4] == (10, 30, 50, 70)
    gaps = {b - a for a, b in zip(satisfaction.MILESTONES, satisfaction.MILESTONES[1:])}
    assert gaps == {20}, gaps
    # 오래 쓴 사람이 상한에 부딪혀 조용히 멈추지 않도록 넉넉해야 한다 (2년 이상)
    assert satisfaction.MILESTONES[-1] >= 730


def test_no_milestone_before_the_first_threshold():
    assert satisfaction.select_milestone(9, recorded=()) is None


def test_first_milestone_fires_exactly_at_ten_days():
    assert satisfaction.select_milestone(10, recorded=()) == 10


def test_picks_the_largest_reached_milestone_not_the_smallest():
    """⛔ 6개월 쓴 사람에게 '10일차'를 물으면 마일스톤이 거짓말이 된다.

    `user_visits` 가 2026-08-11 부터라 과거를 `conversations` 로 메우면 배포 첫날
    50일차 사용자가 곧바로 생긴다 (실측 2명). 그 사람에게는 50 을 물어야 한다.
    """
    assert satisfaction.select_milestone(60, recorded=()) == 50


def test_already_answered_milestone_never_asks_again():
    """응답이든 '나중에'든 행이 남으면 그 임계는 끝이다."""
    assert satisfaction.select_milestone(12, recorded=(10,)) is None


def test_next_milestone_still_fires_after_an_earlier_one():
    assert satisfaction.select_milestone(50, recorded=(10,)) == 50
    assert satisfaction.select_milestone(31, recorded=(10,)) == 30


def test_nothing_pending_until_the_next_twenty_days_pass():
    """방금 물었으면 다음 지점까지는 조용하다."""
    assert satisfaction.select_milestone(69, recorded=(50,)) is None
    assert satisfaction.select_milestone(70, recorded=(50,)) == 70


# ── 처리 대기열에 올릴지 (사용자 지시: 코멘트는 별점과 무관하게 다 받는다) ──

def test_low_rating_reaches_the_inbox_even_without_a_comment():
    assert satisfaction.needs_attention(2, "") is True
    assert satisfaction.needs_attention(3, None) is True


def test_high_rating_with_a_comment_reaches_the_inbox():
    """⛔ 코멘트는 전 별점에서 받는다 — 읽을 게 있으면 개선 재료다."""
    assert satisfaction.needs_attention(5, "표가 더 컸으면 좋겠어요") is True


def test_high_rating_without_a_comment_stays_out_of_the_inbox():
    """읽을 게 없는 행이 대기열을 채우면 대기열이 뜻을 잃는다 (붐따와 같은 규칙)."""
    assert satisfaction.needs_attention(5, "") is False
    assert satisfaction.needs_attention(4, "   ") is False


def test_skipped_response_is_not_an_inbox_item():
    """'나중에' 는 불만이 아니다."""
    assert satisfaction.needs_attention(None, "") is False


# ── 저장 ──────────────────────────────────────────────────────────────────

def _capture(monkeypatch):
    writes = []
    monkeypatch.setattr(satisfaction, "execute",
                        lambda *a, **k: writes.append(a) or 1)
    return writes


def test_record_rejects_an_unknown_milestone(monkeypatch):
    """임계 밖 값이 들어오면 조용히 저장하지 말고 거절한다."""
    writes = _capture(monkeypatch)
    with pytest.raises(ValueError):
        satisfaction.record_response(1, 33, rating=5)   # 20일 간격 밖
    assert writes == []


def test_record_rejects_a_rating_outside_one_to_five(monkeypatch):
    writes = _capture(monkeypatch)
    for bad in (0, 6, -1):
        with pytest.raises(ValueError):
            satisfaction.record_response(1, 10, rating=bad)
    assert writes == []


def test_record_accepts_a_comment_at_every_rating(monkeypatch):
    """별 5개에도 코멘트를 저장한다 (2026-09-02 사용자 지시: '코멘트는 다 받아')."""
    writes = _capture(monkeypatch)
    satisfaction.record_response(1, 10, rating=5, comment="빨라서 좋아요", visit_days=12)
    assert writes, "저장이 일어나지 않았다"
    params = writes[0][1]
    assert "빨라서 좋아요" in params


def test_skip_is_stored_so_the_popup_does_not_return(monkeypatch):
    """'나중에' 도 행을 남긴다 — 안 남기면 다음 접속마다 다시 뜬다."""
    writes = _capture(monkeypatch)
    satisfaction.record_response(1, 10, rating=None, comment="", visit_days=11)
    assert writes, "건너뛰기가 저장되지 않았다"


def test_first_write_wins_so_an_answer_is_never_overwritten(monkeypatch):
    """중복 제출이 와도 이미 남긴 응답을 덮지 않는다."""
    writes = _capture(monkeypatch)
    satisfaction.record_response(1, 10, rating=4, comment="", visit_days=11)
    sql = writes[0][0].upper()
    assert "INSERT" in sql
    assert "IGNORE" in sql or "ON DUPLICATE KEY" in sql


# ── 방문일수는 두 원장의 합집합이다 ────────────────────────────────────────

def test_visit_days_counts_conversation_days_too(monkeypatch):
    """⛔ `user_visits` 만 세면 2026-08-11 이전 사용 이력이 통째로 사라진다.

    실측: 방문 원장은 8/11 부터(3주), `conversations` 는 3/10 부터(6개월).
    합집합으로 세지 않으면 6개월 쓴 사람이 '10일차' 로 잡힌다.
    """
    seen = {}

    def fake_fetch_one(sql, params=None):
        seen["sql"] = sql
        return {"d": 42}

    monkeypatch.setattr(satisfaction, "fetch_one", fake_fetch_one)
    assert satisfaction.visit_days(7) == 42
    sql = seen["sql"].lower()
    assert "user_visits" in sql
    assert "conversations" in sql
    assert "union" in sql


def test_answering_a_later_milestone_closes_the_earlier_ones():
    """⛔ 50일차에 답한 사람에게 다음 세션에 '10일차' 가 뜨면 안 된다.

    지나간 지점을 뒤늦게 묻는 것은 축하가 아니라 오작동으로 읽힌다. 큰 임계에
    답했다면 그보다 작은 임계는 이미 지나간 것이다.
    """
    assert satisfaction.select_milestone(60, recorded=(50,)) is None
    # 다음 지점(70일차)에 닿으면 그때는 다시 물어야 한다
    assert satisfaction.select_milestone(70, recorded=(50,)) == 70


# ── 테스트용 되돌리기 (본인 것만) ──────────────────────────────────────────

def test_reset_only_touches_the_callers_own_rows(monkeypatch):
    """⛔ 남의 응답을 지우는 경로를 만들지 마라 — 조건은 SQL 안에 있어야 한다."""
    seen = {}
    monkeypatch.setattr(satisfaction, "execute",
                        lambda sql, params=None: seen.update(sql=sql, params=params) or 1)
    satisfaction.reset_for_user(7)
    assert "DELETE" in seen["sql"].upper()
    assert "user_id = %s" in seen["sql"]
    assert seen["params"] == (7,)


def test_a_skip_stays_quiet_until_the_next_point_after_today():
    """⛔ 방금 '나중에' 를 누른 사람에게 바로 다음 지점이 뜨면 안 된다.

    실제로 40일차 사용자가 10일차를 건너뛰었는데, 임계를 20일 간격으로 촘촘히
    바꾸자 이미 지나간 30일차가 곧바로 대기에 올랐다. 물어본 **그때의 접속일수**를
    바닥으로 삼아, 그 뒤의 지점부터 다시 묻는다.
    """
    # 40일에 물었고(10일차 행), 지금도 40일 → 조용해야 한다
    assert satisfaction.select_milestone(40, recorded=(10,), asked_at_days=40) is None
    assert satisfaction.select_milestone(49, recorded=(10,), asked_at_days=40) is None
    # 다음 지점(50일차)에 닿으면 그때 묻는다
    assert satisfaction.select_milestone(50, recorded=(10,), asked_at_days=40) == 50


def test_the_floor_comes_from_the_stored_visit_days(monkeypatch):
    """바닥은 저장된 `visit_days` 에서 온다 — 행이 없으면 0 이다."""
    monkeypatch.setattr(satisfaction, "fetch_all",
                        lambda *a, **k: [{"milestone": 10, "visit_days": 40}])
    monkeypatch.setattr(satisfaction, "fetch_one", lambda *a, **k: {"d": 41})
    assert satisfaction.pending_milestone(34) is None
