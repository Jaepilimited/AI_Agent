# -*- coding: utf-8 -*-
"""광고 매체 유실 감시 회귀 (2026-08-31 실측으로 만든 검사).

⛔ 실제로 하루 사이에 일어났다:

      오전  KakaoMoments 있음 (최신 2026-08-27 · 2026-08 집행 594,843원)
      오후  매체 19종 어디에도 없음

   셀라는 두 번 다 **그 시점의 사실**을 답했다. 사람이 두 답을 나란히 놓기
   전까지 아무도 몰랐다 — 에러가 없기 때문이다.

⚠️ `schema_watch` 는 테이블·컬럼을, 점검 감지는 전체 행 수를 본다. 작은 매체가
   통째로 빠지는 것은 **둘 다 못 잡는다** (594,843원짜리 소액 집행이었다).
"""
from __future__ import annotations

from app.core import ad_media_watch as W


_K = {"KakaoMoments": {"rows": 816, "latest": "2026-08-27"}}
_M = {"Meta": {"rows": 44_279, "latest": "2026-08-30"}}
_BOTH = {**_K, **_M}


def _sum(prev, cur, older):
    d = W.diff(prev, cur, older)
    ok, detail = W.summarize({"ok": True, "media": len(cur), **d})
    return ok, detail, d


def test_two_misses_in_a_row_are_a_confirmed_loss():
    """⛔ 두 번째 스냅샷에서는 그 매체가 `prev` 에 없다 — `prev` 만 순회하면
    루프가 아예 닿지 않아 **확정이 영영 안 된다.** 처음에 그렇게 짜서
    "두 번 연속 없음" 이 정상으로 통과했다 (실측으로 잡았다)."""
    ok, detail, d = _sum(prev=_M, cur=_M, older=_BOTH)
    assert ok is False, detail
    assert "KakaoMoments" in detail and "816" in detail, "무엇을 잃었는지 알 수 있어야 한다"
    assert "집행이 없었다" in detail, "왜 위험한지가 빠지면 그냥 로그 한 줄이다"
    assert d["suspected"] == []


def test_a_single_miss_is_not_yet_a_failure():
    """⛔ 이 테이블은 **하루 두 번** 적재된다 (데이터팀 확인). 스냅샷이 적재
    도중에 찍히면 멀쩡한 매체가 잠깐 안 보인다 — 그걸 유실로 올리면 오탐이고,
    오탐이 쌓인 경보는 진짜 유실까지 함께 묻는다."""
    ok, detail, d = _sum(prev=_BOTH, cur=_M, older=_BOTH)
    assert ok is True, "한 번 안 보인 것을 유실로 확정했다"
    assert d["gone"] == [] and d["suspected"]
    assert "적재 중일 수 있음" in detail


def test_a_medium_that_comes_back_is_not_a_loss():
    """재실행으로 복구되면 유실이 아니다 (2026-08-31 실제 경과)."""
    ok, _, d = _sum(prev=_M, cur=_BOTH, older=_BOTH)
    assert ok is True and d["gone"] == [] and d["suspected"] == []


def test_only_one_prior_snapshot_cannot_confirm():
    """⚠️ 비교할 이전 스냅샷이 하나뿐이면 확정할 근거가 없다 — 의심까지만."""
    ok, _, d = _sum(prev=_BOTH, cur=_M, older=None)
    assert ok is True and d["gone"] == [] and d["suspected"]


def test_a_new_medium_is_only_a_note():
    """신규 집행은 정상이다 — 실패로 올리면 매번 뜬다."""
    ok, detail, d = _sum(prev=_M, cur={**_M, "Douyin": {"rows": 24, "latest": "z"}},
                         older=_M)
    assert ok is True and d["gone"] == [] and d["added"]
    assert "Douyin" in detail


def test_a_stalled_medium_is_not_reported():
    """⛔ 집행이 없던 날은 정상이다. 처음에 넣어 봤더니 19종 중 19종이 떴다 —
    매일 뜨는 경보는 곧 아무도 안 본다."""
    ok, detail, d = _sum(prev=_BOTH, cur=dict(_BOTH), older=_BOTH)
    assert ok is True and d == {"gone": [], "suspected": [], "added": []}
    assert "정체" not in detail


def test_todays_silence_is_not_a_loss():
    """⚠️ '오늘 집행이 없는 것' 과 '매체가 사라진 것' 은 다르다 —
    전체 기간의 존재 여부를 본다."""
    prev = {"NaverGFA": {"rows": 823, "latest": "2026-08-30"}}
    cur = {"NaverGFA": {"rows": 823, "latest": "2026-08-30"}}  # 오늘 집행 없음
    assert W.diff(prev, cur, older=prev)["gone"] == []


def test_failures_do_not_look_like_success():
    """⛔ 조회가 실패했는데 '매체 0종 유지' 로 넘어가면 유실을 영영 못 본다."""
    assert W.summarize({"ok": False, "detail": "매체 목록을 읽지 못했다"})[0] is False
    assert W.summarize({"ok": True, "baseline": True,
                        "detail": "기준선 저장 (19개 매체)"})[0] is True


def test_the_watch_is_wired_into_the_daily_checks():
    """⛔ 등록을 빠뜨리면 서버에서 안 돈다 — 만들어 두기만 한 검사가 된다."""
    from app.core import self_check as SC

    assert any(c.id == "ad_media_missing" for c in SC.CHECKS)
    assert "ad_media_snapshot_daily" in SC.EXPECTED_JOBS

    import io
    import os
    main = io.open(os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "app", "main.py"), encoding="utf-8").read()
    assert "_ad_media_snapshot_job" in main and "ad_media_snapshot_daily" in main
    assert "ensure_ad_media_table" in main, "기동 시 테이블 생성이 없다"
