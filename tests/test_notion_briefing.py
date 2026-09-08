# -*- coding: utf-8 -*-
"""브리핑 노션 자동 배송 회귀.

⛔ DB 도 네트워크도 타지 않는다 — 저장 계층과 `_request` 를 monkeypatch 한다.
"""
import pytest

from app.core import jandi_briefing as jb
from app.core import notion_briefing as nb


def test_page_url_validation_reuses_the_engine():
    """⛔ 아무 URL 이나 받으면 서버가 남의 메일 요약을 아무 데나 쓰는 기계가 된다."""
    assert nb.is_valid_page_url(
        "https://www.notion.so/24f1a2b3c4d54e6f8a9b0c1d2e3f4a5b") is True
    assert nb.is_valid_page_url("https://example.com/x") is False
    assert nb.is_valid_page_url("https://skin1004.notion.site/abc") is False
    assert nb.is_valid_page_url("") is False


def test_mask_hides_the_id():
    masked = nb.mask("https://www.notion.so/회의록-24f1a2b3c4d54e6f8a9b0c1d2e3f4a5b")
    assert "24f1a2b3" not in masked
    assert "notion.so" in masked


def test_send_time_choices_come_from_jandi_not_a_copy():
    """⛔ 두 화면이 다른 목록을 보이면 사용자가 혼란스럽다 — 사본을 만들지 않는다."""
    assert nb.SEND_TIME_CHOICES is jb.SEND_TIME_CHOICES


def test_sections_come_from_jandi_not_a_copy():
    assert nb.SECTIONS is jb.SECTIONS


def test_set_target_without_a_time_uses_the_default(monkeypatch):
    """⛔ 첫 등록은 시각을 주지 않는다 — 여기서 죽으면 채팅 등록이 통째로 실패한다."""
    seen = {}

    def fake_execute(sql, params=()):
        seen["params"] = params
        return 1

    monkeypatch.setattr(nb, "execute", fake_execute)
    nb.set_target(7, "https://www.notion.so/24f1a2b3c4d54e6f8a9b0c1d2e3f4a5b")
    assert nb.DEFAULT_SEND_AT in seen["params"]


def test_set_target_keeps_muted_when_none_is_given(monkeypatch):
    """⚠️ 시각만 저장하는 요청이 항목 설정을 지우면 안 된다.

    `None`(안 바꿈)과 `[]`(전부 받기)는 뜻이 다르다.
    """
    seen = {}

    def fake_execute(sql, params=()):
        seen["params"] = params
        return 1

    monkeypatch.setattr(nb, "execute", fake_execute)
    url = "https://www.notion.so/24f1a2b3c4d54e6f8a9b0c1d2e3f4a5b"

    nb.set_target(7, url, muted=None)
    assert seen["params"][-1] == 0        # 기존 설정을 유지한다

    nb.set_target(7, url, muted=[])
    assert seen["params"][-1] == 1        # 빈 목록은 "전부 받기" — 바꾼다


def test_mask_is_empty_for_a_bad_url():
    """가릴 것이 없으면 빈 문자열 — 엉뚱한 문자열을 만들어내지 않는다."""
    assert nb.mask("https://example.com/x") == ""
    assert nb.mask("") == ""


from datetime import date, datetime, time


class _FakeDB:
    """execute/fetch_* 를 가로채 SQL 과 파라미터만 기록한다."""

    def __init__(self):
        self.calls = []
        self.rows = []

    def execute(self, sql, params=()):
        self.calls.append((" ".join(sql.split()), params))
        return 1

    def fetch_all(self, sql, params=()):
        self.calls.append((" ".join(sql.split()), params))
        return self.rows

    def fetch_one(self, sql, params=()):
        self.calls.append((" ".join(sql.split()), params))
        return self.rows[0] if self.rows else None


@pytest.fixture
def db(monkeypatch):
    fake = _FakeDB()
    monkeypatch.setattr(nb, "execute", fake.execute)
    monkeypatch.setattr(nb, "fetch_all", fake.fetch_all)
    monkeypatch.setattr(nb, "fetch_one", fake.fetch_one)
    return fake


def test_enqueue_refuses_an_empty_body(db):
    """빈 브리핑을 보내지 않는다."""
    assert nb.enqueue(7, date(2026, 9, 8), "https://www.notion.so/x", "  ", "제목") is False
    assert db.calls == []


def test_enqueue_refuses_a_bad_url(db):
    assert nb.enqueue(7, date(2026, 9, 8), "https://example.com/x", "본문", "제목") is False
    assert db.calls == []


def test_enqueue_uses_the_date_as_dedup_key(db):
    """⛔ 같은 날 두 번 돌아도 두 번 보내지 않는다.

    ⚠️ 파라미터를 **순서까지** 고정한다 — 값이 목록에 있기만 하면 통과하는
       테스트는 순서가 뒤바뀐 SQL 을 잡지 못한다 (이번에 실제로 그렇게 놓쳤다).
    """
    url = "https://www.notion.so/24f1a2b3c4d54e6f8a9b0c1d2e3f4a5b"
    assert nb.enqueue(7, date(2026, 9, 8), url, "본문",
                      "2026-09-08 출근 브리핑") is True
    sql, params = db.calls[0]
    assert "briefing_notion_outbox" in sql
    assert params == (7, date(2026, 9, 8), "briefing:2026-09-08",
                      "2026-09-08 출근 브리핑", url, "본문", None)


def test_pending_compares_against_the_passed_clock_not_now(db):
    """⛔ `NOW()` 를 쓰면 DB 호스트 TZ 가 바뀔 때 9시간 어긋난다."""
    now = datetime(2026, 9, 8, 9, 0)
    nb.pending(now)
    sql, params = db.calls[0]
    assert "NOW()" not in sql
    assert now in params


def test_pending_also_takes_rows_without_a_send_after(db):
    """⚠️ 즉시 발송분(`지금 대기열에 넣기`)은 시각을 갖지 않는다."""
    nb.pending(datetime(2026, 9, 8, 9, 0))
    sql, _ = db.calls[0]
    assert "send_after IS NULL" in sql


def test_mark_failed_gives_up_after_max_attempts(db):
    nb.mark_failed(11, "노션이 404")
    sql, params = db.calls[0]
    assert "attempts=attempts+1" in sql.replace(" ", "")
    assert nb.MAX_ATTEMPTS in params


def test_mark_failed_computes_status_before_incrementing(db):
    """⛔ SET 절 순서가 의미를 바꾼다 — `status` 가 증가 뒤에 오면 한 번 일찍 포기한다.

    MySQL 은 SET 을 왼쪽부터 평가하고 뒤 절이 앞 절의 **새 값**을 본다.
    """
    nb.mark_failed(11, "노션이 404")
    sql, params = db.calls[0]
    status_at = sql.index("status=IF")
    attempts_at = sql.index("attempts=attempts+1")
    assert status_at < attempts_at, "status 를 attempts 증가보다 먼저 계산해야 한다"
    assert params[0] == nb.MAX_ATTEMPTS


def test_enqueue_does_not_touch_a_row_that_was_already_sent(db):
    """⚠️ 이미 보낸 행은 주소도 본문도 바꾸지 않는다 — 다시 보내지 않으므로 뜻이 없다."""
    nb.enqueue(7, date(2026, 9, 8),
               "https://www.notion.so/24f1a2b3c4d54e6f8a9b0c1d2e3f4a5b",
               "본문", "제목")
    sql, _ = db.calls[0]
    for column in ("page_url", "body", "title", "send_after"):
        assert f"{column}=IF(status='pending'" in sql.replace(" ", ""), \
            f"{column} must be guarded with status='pending' check"


def test_status_counts_can_narrow_to_one_day(db):
    """자가 점검이 읽는 값이다 — 날짜를 주면 그 날짜만 센다."""
    db.rows = [{"status": "pending", "n": 2}, {"status": "sent", "n": 5}]
    assert nb.status_counts() == {"pending": 2, "sent": 5}
    sql, params = db.calls[0]
    assert "WHERE" not in sql

    nb.status_counts(date(2026, 9, 8))
    sql, params = db.calls[1]
    assert "for_date=%s" in sql.replace(" ", "")
    assert params == (date(2026, 9, 8),)


def test_cleanup_only_deletes_older_than_the_cutoff(db):
    """⛔ 기준일 이상은 지우지 않는다 — 오늘 몫을 지우면 그날 브리핑이 사라진다."""
    nb.cleanup(date(2026, 9, 1))
    sql, params = db.calls[0]
    assert "DELETE" in sql
    assert "for_date < %s" in sql.replace("  ", " ")
    assert params == (date(2026, 9, 1),)


from app.core import notion_export as nx


@pytest.fixture
def engine(monkeypatch):
    state = {"saved": [], "target": nx.Target("db-1", "ds-1", {"제목": "title"})}

    def resolve(user_id, url):
        if "boom" in url:
            raise nx.NotionError("not_connected", "연결 없음", 404)
        return state["target"]

    def save(target, title, text, kind="답변", link=""):
        state["saved"].append({"title": title, "text": text, "kind": kind})
        return nx.SaveResult(url="https://notion.so/row-1")

    monkeypatch.setattr(nx, "is_enabled", lambda: True)
    monkeypatch.setattr(nx, "resolve_target", resolve)
    monkeypatch.setattr(nx, "save", save)
    return state


def _row(**over):
    row = {"id": 1, "user_id": 7, "for_date": date(2026, 9, 8),
           "title": "2026-09-08 출근 브리핑",
           "page_url": "https://www.notion.so/24f1a2b3c4d54e6f8a9b0c1d2e3f4a5b",
           "body": "☀️ 오늘의 출근 브리핑", "attempts": 0}
    row.update(over)
    return row


def test_push_saves_and_marks_sent(db, engine, monkeypatch):
    monkeypatch.setattr(nb, "pending", lambda now, limit=50: [_row()])
    marked = []
    monkeypatch.setattr(nb, "mark_sent", lambda i, u: marked.append((i, u)))
    result = nb.push_pending(datetime(2026, 9, 8, 9, 0))
    assert result == {"sent": 1, "failed": 0}
    assert engine["saved"][0]["kind"] == "브리핑"
    assert marked == [(1, "https://notion.so/row-1")]


def test_push_records_the_reason_when_notion_refuses(db, engine, monkeypatch):
    """⛔ 실패를 조용히 넘기면 그 사람 브리핑은 영원히 안 온다."""
    monkeypatch.setattr(nb, "pending",
                        lambda now, limit=50: [_row(page_url="https://www.notion.so/boom1a2b3c4d54e6f8a9b0c1d2e3f4a5b")])
    failed = []
    monkeypatch.setattr(nb, "mark_failed", lambda i, e: failed.append((i, e)))
    result = nb.push_pending(datetime(2026, 9, 8, 9, 0))
    assert result == {"sent": 0, "failed": 1}
    assert "not_connected" in failed[0][1]


def test_push_does_nothing_when_the_feature_is_off(db, monkeypatch):
    """토큰이 없으면 대기열을 건드리지 않는다 — 나중에 켜지면 그대로 나간다."""
    monkeypatch.setattr(nx, "is_enabled", lambda: False)
    called = []
    monkeypatch.setattr(nb, "pending", lambda now, limit=50: called.append(1) or [])
    assert nb.push_pending(datetime(2026, 9, 8, 9, 0)) == {"sent": 0, "failed": 0}
    assert called == []


def test_push_uses_kst_when_no_clock_is_given(db, engine, monkeypatch):
    seen = {}
    monkeypatch.setattr(nb, "pending",
                        lambda now, limit=50: seen.setdefault("now", now) and [])
    nb.push_pending()
    assert seen["now"].year >= 2026


def test_one_bad_row_does_not_strand_the_rest(db, engine, monkeypatch):
    """⛔ 장부 기록이 터져도 뒤에 남은 사람들 브리핑은 계속 나가야 한다."""
    rows = [_row(id=1, user_id=7), _row(id=2, user_id=8)]
    monkeypatch.setattr(nb, "pending", lambda now, limit=50: rows)

    calls = []

    def flaky_mark_sent(outbox_id, row_url):
        calls.append(outbox_id)
        if outbox_id == 1:
            raise RuntimeError("DB 가 잠깐 흔들렸다")

    monkeypatch.setattr(nb, "mark_sent", flaky_mark_sent)
    monkeypatch.setattr(nb, "_remember_sent", lambda user_id: None)

    result = nb.push_pending(datetime(2026, 9, 8, 9, 0))
    assert calls == [1, 2]                     # 두 번째 행까지 처리했다
    assert result == {"sent": 1, "failed": 1}


def test_an_unexpected_error_is_counted_and_recorded(db, engine, monkeypatch):
    """노션이 아닌 이유로 실패해도 화면에 흔적이 남아야 한다."""
    monkeypatch.setattr(nb, "pending", lambda now, limit=50: [_row()])

    def boom(user_id, url):
        raise RuntimeError("소켓이 끊겼다")

    monkeypatch.setattr(nx, "resolve_target", boom)
    failed, remembered = [], []
    monkeypatch.setattr(nb, "mark_failed", lambda i, e: failed.append((i, e)))
    monkeypatch.setattr(nb, "_remember_error", lambda u, e: remembered.append((u, e)))

    assert nb.push_pending(datetime(2026, 9, 8, 9, 0)) == {"sent": 0, "failed": 1}
    assert failed[0][1] == "RuntimeError"
    assert remembered[0][0] == 7


def test_enqueue_notion_skips_when_every_section_is_muted(monkeypatch):
    """⛔ 실릴 것을 전부 끈 사람에게 머리말만 보내지 마라."""
    from app.core import personal_briefing as pb
    from app.db.models import User

    envelope = {"document": {"status": "ready", "for_date": "2026-09-08",
                             "meetings": [{"time": "10:00", "title": "회의",
                                           "urgency": "normal"}]},
                "fx": {}, "business": {}}
    called = []
    monkeypatch.setattr(nb, "enqueue", lambda *a, **k: called.append(1) or True)
    user = User(id=7, email="a@b.c", name="임재필", department="", role="user",
                allowed_models="", ad_user_id=None)
    ok = pb._enqueue_notion(user, envelope, "https://www.notion.so/x", "임재필",
                            muted=["meetings"])
    assert ok is False
    assert called == []


def test_enqueue_notion_titles_the_row_with_the_date(monkeypatch):
    from app.core import personal_briefing as pb
    from app.db.models import User

    envelope = {"document": {"status": "ready", "for_date": "2026-09-08",
                             "weekday": "화",
                             "meetings": [{"time": "10:00", "title": "회의",
                                           "urgency": "normal"}]},
                "fx": {}, "business": {}}
    seen = {}

    def fake_enqueue(user_id, for_date, page_url, body, title, send_after=None):
        seen.update({"title": title, "body": body, "user_id": user_id})
        return True

    monkeypatch.setattr(nb, "enqueue", fake_enqueue)
    user = User(id=7, email="a@b.c", name="임재필", department="", role="user",
                allowed_models="", ad_user_id=None)
    assert pb._enqueue_notion(user, envelope,
                              "https://www.notion.so/24f1a2b3c4d54e6f8a9b0c1d2e3f4a5b",
                              "임재필") is True
    assert seen["title"] == "2026-09-08 출근 브리핑"
    assert "회의" in seen["body"]


def test_broken_send_at_or_date_does_not_crash_the_batch():
    """⛔ 설정 하나가 깨져도 `one(row)` 밖으로 예외가 새면 성공한 브리핑까지
    `failed` 로 잘못 집계된다 — 잔디처럼 두 겹으로 감싸 안전하게 실패한다."""
    from app.core import personal_briefing as pb
    from app.db.models import User

    envelope = {"document": {"status": "ready", "for_date": "not-a-date",
                             "meetings": [{"time": "10:00", "title": "회의",
                                           "urgency": "normal"}]},
                "fx": {}, "business": {}}
    user = User(id=7, email="a@b.c", name="임재필", department="", role="user",
                allowed_models="", ad_user_id=None)
    # 고치기 전에는 `date.fromisoformat("not-a-date")` 의 ValueError 가 그대로
    # 밖으로 나갔다 (RuntimeError·ValueError 전파, 아래에서 실측 확인함).
    assert pb._enqueue_notion(user, envelope, "https://www.notion.so/x",
                              "임재필") is False


def test_run_morning_precompute_reports_notion_queue_count():
    """⛔ 몇 명분을 노션 대기열에 넣었는지 세지 않으면 조용히 0건이 되어도
    아무도 모른다 — 잔디의 `queued` 옆에 나란히 세운다."""
    import asyncio
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from app.core import personal_briefing as pb

    kst = ZoneInfo("Asia/Seoul")
    # 주말 조기 반환 경로 — 실제 DB/네트워크 없이 그대로 부를 수 있다.
    # ⚠️ 경로마다 반환 키가 달라지면 읽는 쪽이 KeyError 를 만난다.
    out = asyncio.run(pb.run_morning_precompute(
        now=datetime(2026, 8, 29, 9, 0, tzinfo=kst)))
    assert out["skipped"] == "weekend"
    assert out["queued_notion"] == 0

    import inspect
    source = inspect.getsource(pb.run_morning_precompute)
    # 정상 경로(비주말)의 반환 dict 에도 같은 키가 있어야 한다.
    assert '"queued_notion": queued_notion' in source
    assert "queued_notion += 1" in source


def test_push_job_is_registered_in_main():
    """⛔ 잡을 안 걸면 대기열이 영원히 안 비워진다 — 에러도 없이."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    source = (root / "app" / "main.py").read_text(encoding="utf-8")
    assert "notion_push_halfhourly" in source
    assert "notion_briefing" in source


def test_push_job_is_watched_by_self_check():
    from app.core.self_check import EXPECTED_JOBS

    assert "notion_push_halfhourly" in EXPECTED_JOBS


def test_self_check_has_a_notion_push_check():
    from app.core import self_check

    assert any(c.id == "notion_push" for c in self_check.CHECKS)


def test_self_check_also_watches_immediate_rows():
    """⛔ 즉시 발송분은 도착 시각이 없다 — 감시에서 빼면 조용히 쌓인다.

    `pending()` 은 그런 행을 일부러 함께 꺼낸다. 감시만 빠지면 짝이 맞지 않는다.
    """
    import inspect

    from app.core import self_check

    source = inspect.getsource(self_check._check_notion_push)
    assert "send_after IS NULL" in source
    assert "created_at" in source


def test_api_hides_the_saved_url_and_serves_the_choices():
    """⛔ 시각·절 목록은 **서버가 단일 소스**다. 프론트에 사본을 두면 조용히 갈린다."""
    from app.api import notion_briefing_api as api

    source = __import__("pathlib").Path(api.__file__).read_text(encoding="utf-8")
    assert "send_time_choices" in source
    assert "sections" in source
    assert "mask(" in source


def test_api_put_keeps_the_saved_url_when_none_is_sent():
    """⛔ 서버가 주소를 가려서 내려주므로, 시각만 바꾸려는 사람은 되붙일 수 없다."""
    from app.api import notion_briefing_api as api

    source = __import__("pathlib").Path(api.__file__).read_text(encoding="utf-8")
    assert "이미 저장된 주소" in source


def test_api_is_registered_in_main():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    source = (root / "app" / "main.py").read_text(encoding="utf-8")
    assert "notion_briefing_api" in source


def test_set_target_keeps_the_chosen_time_when_none_is_given(monkeypatch):
    """⛔ 시각을 빼고 저장하는 요청이 골라 둔 회차를 되돌리면 안 된다.

    채팅 등록이 `set_target(user_id, url)` 로 부르므로, 이미 18:30 으로
    맞춰 둔 사람이 페이지만 바꿔도 08:00 으로 리셋될 수 있었다.
    """
    seen = {}
    monkeypatch.setattr(nb, "execute",
                        lambda sql, params=(): seen.update(
                            {"sql": " ".join(sql.split()), "params": params}) or 1)
    url = "https://www.notion.so/24f1a2b3c4d54e6f8a9b0c1d2e3f4a5b"

    nb.set_target(7, url)                      # 시각 안 줌
    assert "send_at=IF(%s," in seen["sql"].replace(" ", "")
    assert 0 in seen["params"]                 # 바꾸지 않는다는 플래그

    nb.set_target(7, url, send_at="09:00")     # 시각 줌
    assert 1 in seen["params"]


def test_put_returns_the_refreshed_state_like_jandi():
    """화면이 저장 직후 한 번 더 GET 하지 않아도 되게."""
    import inspect

    from app.api import notion_briefing_api as api

    source = inspect.getsource(api.put_my_notion_target)
    assert "get_my_notion_target" in source


def test_put_accepts_the_same_time_forms_the_store_accepts():
    """⚠️ 화면과 저장이 서로 다른 규칙을 가지면 안 된다."""
    import inspect

    from app.api import notion_briefing_api as api

    source = inspect.getsource(api.put_my_notion_target)
    assert "normalize_send_at" in source


def test_set_target_flags_land_in_their_own_slots(monkeypatch):
    """⛔ 조건부 플래그가 둘이다 — 뒤바뀌면 "시각 그대로 둬" 가 **항목 설정을 지운다.**

    존재만 보는 단언(`1 in params`)은 뒤바뀐 순서를 잡지 못한다.
    두 플래그가 서로 다른 값이 되는 호출로 자리를 고정한다.
    """
    seen = {}
    monkeypatch.setattr(nb, "execute",
                        lambda sql, params=(): seen.update(
                            {"sql": " ".join(sql.split()), "params": params}) or 1)
    url = "https://www.notion.so/24f1a2b3c4d54e6f8a9b0c1d2e3f4a5b"

    # 시각은 안 바꾸고(0) 항목만 바꾼다(1)
    nb.set_target(7, url, muted=["mail"])
    assert seen["params"][-2:] == (0, 1), (
        f"시각 플래그=0, 항목 플래그=1 이어야 한다: {seen['params'][-2:]}")

    # 반대로: 시각만 바꾸고(1) 항목은 그대로(0)
    nb.set_target(7, url, send_at="09:00")
    assert seen["params"][-2:] == (1, 0), (
        f"시각 플래그=1, 항목 플래그=0 이어야 한다: {seen['params'][-2:]}")


def test_frontend_does_not_hardcode_choices_or_sections():
    """⛔ 프론트에 사본을 두면 절이 하나 늘 때 화면에서 통째로 사라진다."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    js = (root / "app" / "frontend" / "personal-briefing.js").read_text(encoding="utf-8")
    assert "/api/personal-briefing/notion" in js
    assert "send_time_choices" in js          # 서버가 준 목록을 그린다
    # 시각 목록을 손으로 적지 않았는가
    assert "08:30" not in js


def test_frontend_notion_settings_are_wired_into_the_dialog():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    js = (root / "app" / "frontend" / "personal-briefing.js").read_text(encoding="utf-8")
    assert "appendNotionSettings" in js
    # 다이얼로그를 만드는 곳에서 실제로 불린다
    assert js.count("appendNotionSettings") >= 2


def test_frontend_styles_live_in_the_stylesheet_not_inline():
    """⛔ 테마를 타야 하는 스타일을 JS 인라인으로 두면 테마 전환에서 조용히 빠진다."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    css = (root / "app" / "static" / "style.css").read_text(encoding="utf-8")
    assert ".briefing-notion" in css


def test_frontend_warns_that_a_shared_page_shows_mail_titles():
    """⛔ 브리핑에는 메일 제목이 들어간다 — 노션 페이지는 공유가 쉽다 (스펙 §6)."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    js = (root / "app" / "frontend" / "personal-briefing.js").read_text(encoding="utf-8")
    assert "메일 제목" in js
    assert "연결" in js          # 연결 붙이는 법도 함께 안내한다
