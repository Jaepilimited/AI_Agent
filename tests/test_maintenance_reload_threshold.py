"""점검 모드 자동 감지 — "행이 하나라도 줄면" 이 아니라 **의미 있게** 줄어야 켜진다.

2026-09-28 프로덕션 30일 실측: 자동 활성 406회 중 물류 244회(60%)가 전부 1행(0.035%) 감소.
사람이 시트에서 행 하나를 지울 때마다 3분씩 점검 모드가 켜졌다. 진짜 재적재는 1%~100% 다.
"""
import inspect

import pytest

from app.core import safety
from app.core.safety import _UPDATE_WINDOW_SECONDS, looks_like_reload

RECENT = 1  # 초 전 수정
STALE = _UPDATE_WINDOW_SECONDS + 1


@pytest.mark.parametrize("row_count,baseline", [
    (2_873, 2_874),          # 물류: 1행 삭제 (실측 244회의 모양)
    (2_530 - 1, 2_530),      # LOG 시절 같은 모양
    (1_298_344 - 12_337, 1_298_344),  # 제품: 0.95% (실측 최대)
    (33_870_494 - 5_040, 33_870_494),  # 매출: 0.015%
    (4_299_234 - 21_170, 4_299_234),   # 메타광고: 0.49%
])
def test_small_drops_are_not_a_reload(row_count, baseline):
    assert looks_like_reload(row_count, baseline, RECENT) is False


@pytest.mark.parametrize("row_count,baseline", [
    (143_064 - 16_578, 143_064),   # 광고 11.6%
    (105_233 - 61_586, 105_233),   # 마케팅 58.5%
    (0, 291_965),                  # 인플루언서 100% (truncate 직후)
    (19_238 - 200, 19_238),        # 아마존검색 1.04% — 실측 재적재 중 가장 작은 것
    (14_270_413 - 441_462, 14_270_413),  # 플랫폼 3.1%
    (1_263 - 61, 1_263),           # 해외CS 4.8%
])
def test_real_reloads_are_still_caught(row_count, baseline):
    assert looks_like_reload(row_count, baseline, RECENT) is True


def test_exactly_one_percent_is_the_edge():
    assert looks_like_reload(9_900, 10_000, RECENT) is True
    assert looks_like_reload(9_901, 10_000, RECENT) is False


def test_an_old_modification_never_counts_even_if_rows_dropped():
    assert looks_like_reload(0, 10_000, STALE) is False


def test_unknowns_are_not_a_reload():
    assert looks_like_reload(None, 10_000, RECENT) is False
    assert looks_like_reload(9_000, None, RECENT) is False
    assert looks_like_reload(9_000, 0, RECENT) is False
    assert looks_like_reload(9_000, 10_000, None) is False


def test_growth_or_no_change_is_not_a_reload():
    assert looks_like_reload(10_000, 10_000, RECENT) is False
    assert looks_like_reload(10_500, 10_000, RECENT) is False


def test_the_loop_uses_the_shared_judgement_not_its_own_comparison():
    """판정을 루프 안에 다시 적으면 임계가 두 벌이 된다 — 한쪽만 고쳐진다."""
    src = inspect.getsource(safety.maintenance_auto_detect_loop)
    # ⚠️ Detection 1 의 주석 안에 "Detection 2" 라는 말이 나온다 — 절 머리말로 자른다
    detect1 = src.split("# Detection 1")[1].split("# Detection 2")[0]
    assert "looks_like_reload(" in detect1
    assert "row_count < _base" not in detect1
