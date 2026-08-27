# -*- coding: utf-8 -*-
"""재시작 경보가 **배포와 크래시 루프를 가르는가**.

⛔ 하루 총량으로 재면 배포가 많은 날 그것만으로 상한을 넘는다 — 실제로 개발 이틀에
   24시간 55회가 찍혀 경보가 울렸는데, 시간대를 보니 업무 시간(08~16시)에만 몰려
   있고 밤새 0회였다. **배포다.** 할 일이 없는데 울리는 알림은 곧 무시당한다.

⚠️ 가르는 것은 양이 아니라 **끈질김**이다. 배포는 사람이 일하는 동안 몰렸다 그친다.
   루프는 쉬지 않는다 (2026-08-13~24 사고: 11일간 시간당 약 9회).
"""
import app.core.self_check as sc


def _run(hourly_counts, monkeypatch):
    rows = [{"h": f"2026-08-27 {i:02d}", "c": n} for i, n in enumerate(hourly_counts)]
    monkeypatch.setattr(sc, "fetch_all", lambda *a, **k: rows)
    return sc._check_restart_loop()


def test_a_day_of_deploys_does_not_trip_the_alarm(monkeypatch):
    """실측(2026-08-26): 08~16시에 8·7·17·6·4·2·4·6·1 회, 그 외 0회 — 총 55회."""
    deploys = [0, 0, 0, 0, 0, 0, 0, 0, 8, 7, 17, 6, 4, 2, 4, 6, 1, 0, 0, 0, 0, 0, 0, 0]
    result = _run(deploys[-12:], monkeypatch)
    assert result.ok, result.detail


def test_a_real_crash_loop_still_trips(monkeypatch):
    """예전 사고 모양 — 시간당 9회가 쉬지 않고 이어진다."""
    result = _run([9] * 12, monkeypatch)
    assert not result.ok
    assert "크래시 루프" in result.detail


def test_a_quiet_night_after_deploys_is_the_tell(monkeypatch):
    """배포가 아무리 많아도 **그친다** — 그친 뒤 12시간 창에는 거의 안 남는다."""
    result = _run([17, 6, 4, 2, 0, 0, 0, 0, 0, 0, 0, 0], monkeypatch)
    assert result.ok, result.detail


def test_the_verdict_does_not_use_a_daily_total(monkeypatch):
    """⛔ 총량 상한으로 되돌리지 마라 — 그것이 배포에 걸리던 이유다."""
    import inspect

    src = inspect.getsource(sc._check_restart_loop)
    assert "INTERVAL 12 HOUR" in src
    assert "_RESTART_LOOP_BUSY_HOURS" in src
    assert "DAILY_LIMIT" not in src


def test_the_query_has_no_percent_format_specifier():
    """⛔ `DATE_FORMAT(ts, '%Y-%m-%d %H')` 을 쓰면 pymysql 이 `%` 를 포맷 지시자로 읽어
       터지고, `except` 가 그것을 삼켜 **검사가 영원히 "판정 보류" 로 통과한다.**
       2026-08-27 실제로 그렇게 넣었다가 프로덕션에서 잡았다 — 에러가 아니라
       조용한 통과라 테스트로 막지 않으면 다시 들어온다.
    """
    import inspect

    src = inspect.getsource(sc._check_restart_loop)
    query = src.split("fetch_all(", 1)[1].split(")", 1)[0]
    assert "%" not in query, query.strip()
