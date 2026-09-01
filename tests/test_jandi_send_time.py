"""잔디 브리핑 도착 시각 — 사용자가 고른 시각에 꺼내가는지.

여기서 잡으려는 것은 전부 **에러 없이 어긋나는** 종류다:

- DB 세션 타임존이 UTC 면 `NOW()` 비교가 9시간 틀린다. 에러는 안 나고
  브리핑만 엉뚱한 때 가거나 아예 안 간다 → 기준 시각은 파이썬이 KST 로 만들어
  **파라미터로 넘긴다**.
- `send_after` 필터가 알림(`send_after IS NULL`)까지 걸러 버리면 회신·공유 알림이
  조용히 멈춘다 → 두 방향을 함께 고정한다.
- 선택지 목록을 프론트가 따로 갖고 있으면 릴레이 회차가 바뀔 때 갈린다
  (`@@` 데이터소스 목록이 갈렸던 그 사고와 같은 종류) → 서버가 단일 소스다.
"""

from datetime import date, datetime, time
from pathlib import Path

import pytest

from app.core import jandi_briefing

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "app/frontend/personal-briefing.js"


# ── 선택지: 릴레이 회차와 같아야 한다 ────────────────────────────────────────

def test_choices_cover_the_relay_window_every_half_hour():
    """DB_PC 예약작업 `SKIN1004-Jandi-Briefing`: 08:00 시작 · 30분 간격 · 10h35m.

    그래서 실제로 꺼내가는 회차는 08:00~18:30 이다. 목록이 이보다 넓으면
    사용자가 **영영 오지 않는 시각**을 고를 수 있다.
    """

    choices = jandi_briefing.SEND_TIME_CHOICES

    assert choices[0] == "08:00"
    assert choices[-1] == "18:30"
    assert len(choices) == 22
    assert all(item.endswith(":00") or item.endswith(":30") for item in choices)


def test_default_send_at_keeps_todays_behaviour():
    """기존 등록자는 지금과 똑같이 첫 회차에 받아야 한다 — 바꾼 적 없는 설정이다."""

    assert jandi_briefing.DEFAULT_SEND_AT == time(8, 0)
    assert "08:00" in jandi_briefing.SEND_TIME_CHOICES


# ── 검증: 저장 경계에서 막는다 ───────────────────────────────────────────────

@pytest.mark.parametrize("value", ["08:10", "07:30", "19:00", "24:00", "", "아침", "8"])
def test_normalize_rejects_times_the_relay_never_visits(value):
    with pytest.raises(ValueError):
        jandi_briefing.normalize_send_at(value)


@pytest.mark.parametrize(
    "value,expected",
    [("08:00", time(8, 0)), ("18:30", time(18, 30)), ("09:30", time(9, 30))],
)
def test_normalize_accepts_relay_visits(value, expected):
    assert jandi_briefing.normalize_send_at(value) == expected


def test_normalize_accepts_time_objects_and_db_strings():
    """DB 는 TIME 을 `08:00:00` 으로도, `datetime.time` 으로도 돌려준다."""

    assert jandi_briefing.normalize_send_at(time(9, 0)) == time(9, 0)
    assert jandi_briefing.normalize_send_at("09:00:00") == time(9, 0)


def test_normalize_accepts_pymysql_timedelta():
    """⚠️ PyMySQL 은 TIME 컬럼을 `timedelta` 로 돌려준다 — 읽어올 때마다 지나간다."""

    from datetime import timedelta

    assert jandi_briefing.normalize_send_at(timedelta(hours=8)) == time(8, 0)
    assert jandi_briefing.normalize_send_at(timedelta(hours=18, minutes=30)) == time(18, 30)


def test_set_webhook_refuses_off_grid_time_without_touching_db(monkeypatch):
    """호출부가 검증을 빠뜨려도 여기서 막힌다 — `is_valid_webhook` 과 같은 사상이다."""

    calls = []
    monkeypatch.setattr(jandi_briefing, "execute", lambda *args: calls.append(args))

    with pytest.raises(ValueError):
        jandi_briefing.set_webhook(
            7, "https://wh.jandi.com/connect-api/webhook/1/abcdEFGH", send_at="08:10",
        )

    assert calls == []


def test_set_webhook_stores_the_chosen_time(monkeypatch):
    calls = []
    monkeypatch.setattr(jandi_briefing, "execute", lambda *args: calls.append(args))

    jandi_briefing.set_webhook(
        7, "https://wh.jandi.com/connect-api/webhook/1/abcdEFGH", send_at="09:30",
    )

    assert len(calls) == 1
    assert time(9, 30) in calls[0][1]


# ── send_after: KST 벽시계로 만든다 ─────────────────────────────────────────

def test_send_after_is_the_wall_clock_on_the_briefing_date():
    assert jandi_briefing.send_after_for(date(2026, 9, 1), time(9, 30)) == datetime(
        2026, 9, 1, 9, 30
    )


def test_enqueue_passes_send_after_through(monkeypatch):
    captured = {}

    def fake_execute(sql, params):
        captured["sql"] = sql
        captured["params"] = params
        return 1

    monkeypatch.setattr(jandi_briefing, "execute", fake_execute)
    jandi_briefing.enqueue(
        7, date(2026, 9, 1), "https://wh.jandi.com/connect-api/webhook/1/abcdEFGH",
        "본문", send_after=datetime(2026, 9, 1, 9, 30),
    )

    assert "send_after" in captured["sql"]
    assert datetime(2026, 9, 1, 9, 30) in captured["params"]


# ── pending(): 시각이 된 것만, 그러나 알림은 늘 ─────────────────────────────

def _capture_pending(monkeypatch):
    captured = {}

    def fake_fetch_all(sql, params):
        captured["sql"] = sql
        captured["params"] = params
        return []

    monkeypatch.setattr(jandi_briefing, "fetch_all", fake_fetch_all)
    return captured


def test_pending_compares_against_a_python_kst_clock_not_db_now(monkeypatch):
    """⛔ `NOW()` 를 쓰면 DB 세션 타임존이 UTC 일 때 9시간 어긋난다.

    에러가 아니라 **브리핑이 조용히 안 가는 것**으로 나타난다.
    """

    captured = _capture_pending(monkeypatch)
    jandi_briefing.pending()

    assert "NOW()" not in captured["sql"]
    stamps = [value for value in captured["params"] if isinstance(value, datetime)]
    assert len(stamps) == 1
    assert stamps[0].tzinfo is None
    # KST 벽시계와 같은 날짜여야 한다 (UTC 를 넘겨받으면 이 창을 벗어난다).
    from zoneinfo import ZoneInfo

    now_kst = datetime.now(ZoneInfo("Asia/Seoul")).replace(tzinfo=None)
    assert abs((stamps[0] - now_kst).total_seconds()) < 60


def test_pending_never_holds_back_rows_without_a_send_time(monkeypatch):
    """셀라 알림(`send_after IS NULL`)은 지금처럼 다음 회차에 그대로 나가야 한다.

    여기가 막히면 보고서 공유·의견 회신이 **에러 없이** 멈춘다.
    """

    captured = _capture_pending(monkeypatch)
    jandi_briefing.pending()

    condition = captured["sql"].replace("\n", " ")
    assert "send_after IS NULL" in condition
    assert "send_after <= %s" in condition


def test_pending_still_bounds_attempts_and_limit(monkeypatch):
    """새 조건을 넣다가 기존 방어(재시도 한계·건수)를 떨어뜨리지 않는다."""

    captured = _capture_pending(monkeypatch)
    jandi_briefing.pending(limit=10)

    assert jandi_briefing.MAX_ATTEMPTS in captured["params"]
    assert 10 in captured["params"]


def test_enabled_recipients_carries_the_send_time(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        jandi_briefing, "fetch_all",
        lambda sql, *args: captured.setdefault("sql", sql) or [],
    )
    jandi_briefing.enabled_recipients()

    assert "send_at" in captured["sql"]


# ── 프론트: 목록을 따로 갖지 않는다 ─────────────────────────────────────────

def test_frontend_does_not_keep_its_own_copy_of_the_time_list():
    """⛔ 프론트에 시각 목록을 하드코딩하면 릴레이 회차가 바뀔 때 조용히 갈린다.

    `@@` 데이터소스 목록이 서버·프론트 두 벌이라 어긋났던 것과 같은 종류다.
    """

    source = FRONTEND.read_text(encoding="utf-8")

    assert "send_time_choices" in source, "서버가 준 목록으로 채워야 한다"
    for literal in ('"18:30"', "'18:30'", '"08:30"', "'08:30'"):
        assert literal not in source, f"시각 목록을 프론트가 갖고 있다: {literal}"


def test_frontend_help_no_longer_promises_a_fixed_hour():
    """안내문이 `아침 9시` 라고 적혀 있었는데 실제 발송은 08:00 이었다 —
    시각을 고를 수 있게 되면 더더욱 고정 문구를 두면 안 된다."""

    source = FRONTEND.read_text(encoding="utf-8")

    assert "매일 아침 9시에" not in source


# ── API: 화면이 고를 수 있는 형태로 내려주는가 ───────────────────────────────

def _client(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api.auth_middleware import get_current_user
    from app.api.jandi_briefing_api import router
    from app.db.models import User

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: User(
        id=7, email="o@example.com", name="O", department="D", role="user",
        allowed_models="skin1004-Analysis",
    )
    return TestClient(app)


def test_get_hands_the_choice_list_to_the_screen_even_before_registering(monkeypatch):
    monkeypatch.setattr(jandi_briefing, "get_webhook", lambda user_id: None)

    body = _client(monkeypatch).get("/api/personal-briefing/jandi").json()

    assert body["registered"] is False
    assert body["send_time_choices"] == jandi_briefing.SEND_TIME_CHOICES
    assert body["send_at"] == "08:00"


def test_get_reports_the_stored_time_as_hhmm(monkeypatch):
    from datetime import timedelta

    monkeypatch.setattr(jandi_briefing, "get_webhook", lambda user_id: {
        "user_id": 7, "webhook_url": "https://wh.jandi.com/connect-api/webhook/1/abcdEFGH",
        "enabled": 1, "send_at": timedelta(hours=9, minutes=30),
        "last_sent_at": None, "last_error": "",
    })

    body = _client(monkeypatch).get("/api/personal-briefing/jandi").json()

    assert body["send_at"] == "09:30"


def test_put_rejects_a_time_the_relay_never_visits(monkeypatch):
    monkeypatch.setattr(jandi_briefing, "set_webhook", lambda *a, **k: None)

    response = _client(monkeypatch).put("/api/personal-briefing/jandi", json={
        "webhook_url": "https://wh.jandi.com/connect-api/webhook/1/abcdEFGH",
        "send_at": "08:10",
    })

    assert response.status_code == 400
    assert "30분" in response.json()["detail"]


def test_put_can_change_only_the_time_without_resending_the_secret(monkeypatch):
    """⛔ 주소는 가려서 내려주므로 사용자는 되붙일 수 없다 — 비워 보내면 저장된 것을 쓴다."""

    stored = "https://wh.jandi.com/connect-api/webhook/1/abcdEFGH"
    saved = {}
    monkeypatch.setattr(jandi_briefing, "get_webhook", lambda user_id: {
        "user_id": 7, "webhook_url": stored, "enabled": 1,
        "send_at": "08:00", "last_sent_at": None, "last_error": "",
    })
    monkeypatch.setattr(
        jandi_briefing, "set_webhook",
        lambda user_id, url, enabled=True, send_at=None, muted=None: saved.update(
            url=url, send_at=send_at, muted=muted),
    )
    monkeypatch.setattr(jandi_briefing, "reschedule_pending", lambda *a, **k: 0)

    response = _client(monkeypatch).put(
        "/api/personal-briefing/jandi", json={"send_at": "10:00"})

    assert response.status_code == 200
    # ⛔ `muted=None` 이어야 한다 — 시각만 바꾸는 저장이 받을 항목 설정을 지우면 안 된다.
    #    빈 목록(`[]`)은 "전부 받기" 라 뜻이 완전히 다르다.
    assert saved == {"url": stored, "send_at": time(10, 0), "muted": None}


def test_put_without_an_address_or_a_registration_says_so(monkeypatch):
    monkeypatch.setattr(jandi_briefing, "get_webhook", lambda user_id: None)

    response = _client(monkeypatch).put(
        "/api/personal-briefing/jandi", json={"send_at": "10:00"})

    assert response.status_code == 400


# ── 자가 점검: 기다리는 중인 것을 밀린 것으로 세지 않는다 ────────────────────

def test_self_check_does_not_count_items_still_waiting_for_their_time(monkeypatch):
    """⚠️ 18:30 을 고른 사람의 브리핑은 07:30 에 만들어져 11시간 대기한다.

    그것을 '밀렸다' 로 세면 자가 점검이 **매일** 실패하고, 매일 뜨는 경고는
    곧 아무도 안 읽는다 (알림을 상태 변화로만 보내는 이유와 같다).
    """

    from app.core import self_check

    seen = []

    def fake_fetch_one(sql, params=None):
        seen.append((sql, params))
        if "user_jandi_webhooks" in sql:
            return {"c": 3}
        return {"c": 0}

    monkeypatch.setattr(self_check, "fetch_one", fake_fetch_one)
    monkeypatch.setattr(
        jandi_briefing, "status_counts",
        lambda *a, **k: {"pending": 1, "sent": 5, "failed": 0},
    )

    result = self_check._check_jandi_relay()

    assert result.ok
    stuck_sql, stuck_params = seen[-1]
    assert "send_after IS NULL" in stuck_sql
    assert "send_after <= %s" in stuck_sql
    # 여유 6시간은 도착 시각 기준으로도 그대로 걸린다.
    assert isinstance(stuck_params[0], datetime)
    assert (jandi_briefing.now_kst() - stuck_params[0]).total_seconds() == pytest.approx(
        6 * 3600, abs=60
    )


# ── 시각을 바꾸면 아직 안 나간 오늘 몫도 따라온다 ────────────────────────────

def test_reschedule_moves_only_pending_briefings(monkeypatch):
    """⚠️ 알림은 시각을 갖지 않는다 — 함께 옮기면 급한 회신이 저녁으로 밀린다."""

    captured = {}

    def fake_execute(sql, params):
        captured["sql"] = sql
        captured["params"] = params
        return 1

    monkeypatch.setattr(jandi_briefing, "execute", fake_execute)
    jandi_briefing.reschedule_pending(7, "10:00")

    assert "TIMESTAMP(for_date, %s)" in captured["sql"]
    assert "status = 'pending'" in captured["sql"]
    assert "kind = 'briefing'" in captured["sql"]
    assert "NOW()" not in captured["sql"]
    assert captured["params"][0] == time(10, 0)


def test_reschedule_refuses_an_off_grid_time(monkeypatch):
    monkeypatch.setattr(jandi_briefing, "execute", lambda *a: pytest.fail("must not run"))

    with pytest.raises(ValueError):
        jandi_briefing.reschedule_pending(7, "08:10")


def test_put_moves_todays_pending_briefing_to_the_new_time(monkeypatch):
    """⛔ 이게 없으면 "고쳤는데 그대로" 가 된다 — 화면과 실제 도착이 하루 어긋난다."""

    moved = {}
    monkeypatch.setattr(jandi_briefing, "get_webhook", lambda user_id: {
        "user_id": 7, "webhook_url": "https://wh.jandi.com/connect-api/webhook/1/abcdEFGH",
        "enabled": 1, "send_at": "08:00", "last_sent_at": None, "last_error": "",
    })
    monkeypatch.setattr(jandi_briefing, "set_webhook", lambda *a, **k: None)
    monkeypatch.setattr(
        jandi_briefing, "reschedule_pending",
        lambda user_id, send_at: moved.update(user_id=user_id, send_at=send_at) or 1,
    )

    _client(monkeypatch).put("/api/personal-briefing/jandi", json={"send_at": "10:00"})

    assert moved == {"user_id": 7, "send_at": time(10, 0)}
