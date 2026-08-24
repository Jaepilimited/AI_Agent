"""크래시 루프가 '부팅 중'으로 위장하는 것을 막는 회귀 테스트.

2026-08-13 ~ 08-24 실제 사고: PM2 밖 고아 uvicorn 두 개가 3000/3001 을 점유해
PM2 가 관리하는 프로세스는 bind 에 실패하고 죽기를 **56,833회** 반복했다.
그런데 워치독은 11일 내내 이렇게만 적었다:

    [PROD] 부팅 유예 구간 (마지막 시작 후 8s, 이상 지속 0s) — health 실패를 카운트하지 않음

구멍이 세 개였고 셋 다 "정상처럼 보이는" 실패였다:

  1. `still_booting` 이 **경과시간만** 봤다. 유예(60s)보다 빨리 죽는 루프는
     볼 때마다 uptime 이 한 자리 초라 **영원히 유예를 받는다**.
  2. `unhealthy_since` 는 health 가 한 번이라도 200 이면 None 으로 초기화된다.
     고아가 간헐적으로 200 을 주는 동안 `MAX_UNHEALTHY_SECONDS`(180s) 는
     **누적되지 못했다**.
  3. 고아 감지의 `ORPHAN_RESTART_JUMP_THRESHOLD = 10` 은 체크 1회당 증가분을 본다.
     prod·dev 가 번갈아 죽어 앱당 약 33초에 1회 → 실제 루프 주기(약 4분)당 증가는
     **약 7**. 10 을 못 넘겨 한 번도 발동하지 않았다.

핵심 규칙: **재시작 카운터가 늘고 있으면 그건 부팅 중이 아니라 루프다.**
정상 부팅은 카운터가 고정된 채 uptime 만 흐른다.
"""

import pytest

from scripts import server_watchdog as wd


# ── 부팅 유예 판정 ──────────────────────────────────────────────────────────


def test_normal_boot_gets_grace():
    """정상 부팅: 카운터 고정 + uptime 이 유예 안 → 유예를 준다."""
    assert wd.should_grant_boot_grace(
        proc_status="online", uptime_elapsed=20.0,
        restart_count=5, prev_restart_count=5,
    ) is True


def test_crash_loop_is_denied_grace_even_when_uptime_looks_fresh():
    """⛔ 이번 사고의 핵심 — uptime 이 8초라도 카운터가 늘었으면 유예 금지."""
    assert wd.should_grant_boot_grace(
        proc_status="online", uptime_elapsed=8.0,
        restart_count=12, prev_restart_count=5,
    ) is False


def test_single_restart_between_checks_also_denies_grace():
    """증가폭이 1이어도 재시작한 것이다 — 임계값을 두지 않는다.

    임계값 10 을 뒀다가 실제 증가분 7 을 놓친 것이 이번 사고다.
    """
    assert wd.should_grant_boot_grace(
        proc_status="online", uptime_elapsed=3.0,
        restart_count=6, prev_restart_count=5,
    ) is False


def test_uptime_beyond_grace_window_denies_grace():
    assert wd.should_grant_boot_grace(
        proc_status="online", uptime_elapsed=wd.BOOT_GRACE_SECONDS + 1,
        restart_count=5, prev_restart_count=5,
    ) is False


@pytest.mark.parametrize("status", ["waiting restart", "errored", "stopped", "missing"])
def test_non_online_status_denies_grace(status):
    assert wd.should_grant_boot_grace(
        proc_status=status, uptime_elapsed=5.0,
        restart_count=5, prev_restart_count=5,
    ) is False


def test_first_check_has_no_baseline_so_grace_is_allowed():
    """첫 체크엔 비교 대상이 없다 — 여기서 유예를 막으면 기동 직후 오탐이 난다."""
    assert wd.should_grant_boot_grace(
        proc_status="online", uptime_elapsed=5.0,
        restart_count=5, prev_restart_count=None,
    ) is True


# ── 크래시 루프 감지 (health 와 무관해야 한다) ──────────────────────────────


def test_sustained_restart_growth_is_a_crash_loop():
    """연속으로 카운터가 늘면 루프다. health 200 여부를 보지 않는다.

    고아가 포트를 쥐고 200 을 주는 동안에도 잡혀야 한다 — 그게 이번 사고다.
    """
    assert wd.is_crash_looping(wd.CRASH_LOOP_STREAK) is True
    assert wd.is_crash_looping(wd.CRASH_LOOP_STREAK + 3) is True


def test_short_growth_streak_is_not_yet_a_crash_loop():
    """수동 재시작·배포 1~2회를 루프로 오인하면 안 된다."""
    assert wd.is_crash_looping(0) is False
    assert wd.is_crash_looping(wd.CRASH_LOOP_STREAK - 1) is False


# ── 재시작 관측: 루프와 '정상 배포' 를 가른다 ───────────────────────────────


def test_fresh_restart_counts_toward_streak():
    """카운터가 늘었고 지금도 갓 시작 → 루프 후보."""
    assert wd.restarted_since_last_check(
        restart_count=12, prev_restart_count=5, uptime_elapsed=8.0,
    ) is True


def test_deploy_that_stays_up_does_not_count():
    """⚠️ 오탐 방지 — 배포·수동 restart 는 카운터가 오른 뒤 그대로 떠 있다.

    이걸 루프로 세면 워치독이 멀쩡한 서버를 delete→kill→start 한다.
    """
    assert wd.restarted_since_last_check(
        restart_count=6, prev_restart_count=5,
        uptime_elapsed=wd.BOOT_GRACE_SECONDS + 30,
    ) is False


def test_no_restart_does_not_count():
    assert wd.restarted_since_last_check(
        restart_count=5, prev_restart_count=5, uptime_elapsed=3.0,
    ) is False


def test_first_check_does_not_count():
    assert wd.restarted_since_last_check(
        restart_count=5, prev_restart_count=None, uptime_elapsed=3.0,
    ) is False


def test_incident_replay_three_consecutive_checks_trigger_recovery():
    """실제 사고 재현: 체크마다 카운터가 늘고 uptime 은 한 자리 초.

    고아가 200 을 주고 있어도(health 무관) 3회째에 크래시 루프로 확정돼야 한다.
    """
    streak = 0
    observed = [(20051, 8.0), (20065, 3.0), (20079, 12.0)]  # 실측 로그와 같은 모양
    prev = 20037
    for count, uptime in observed:
        if wd.restarted_since_last_check(count, prev, uptime):
            streak += 1
        else:
            streak = 0
        prev = count
    assert wd.is_crash_looping(streak) is True


def test_crash_loop_streak_threshold_is_small_enough_to_catch_real_incident():
    """실측 근거: 앱당 약 33초에 1회 재시작, 루프 주기 약 4분.

    연속 3회 체크면 약 12분 안에 잡힌다. 11일을 못 잡던 것과 비교할 것.
    """
    assert 2 <= wd.CRASH_LOOP_STREAK <= 4


# ── 두 번째 방어선: 워치독이 또 눈이 멀어도 자가 점검이 본다 ────────────────


def test_self_check_watches_restart_loop_independently():
    """⚠️ 워치독은 그 서버 안에서만 돈다. 자가 점검은 DB 를 보므로 서버와 무관하다.

    이번 사고는 워치독 **하나**에만 의존했다가 11일을 놓쳤다. 방어선을 둘로 둔다.
    """
    from app.core import self_check

    ids = {c.id for c in self_check.CHECKS}
    assert "restart_loop" in ids, "재시작 루프 검사가 자가 점검에서 빠졌다"

    check = next(c for c in self_check.CHECKS if c.id == "restart_loop")
    assert check.severity == self_check.SEV_CRITICAL


def test_self_check_restart_threshold_separates_normal_from_incident():
    """임계값은 실측 사이에 있어야 한다 — 정상 최대 26건, 사고 약 5,300건.

    위로 붙이면 미탐, 아래로 붙이면 배포 잦은 날 오탐이 난다.
    """
    from app.core.self_check import _RESTART_LOOP_DAILY_LIMIT

    assert 26 < _RESTART_LOOP_DAILY_LIMIT < 500
