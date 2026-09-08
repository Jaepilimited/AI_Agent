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
    """⛔ 같은 날 두 번 돌아도 두 번 보내지 않는다."""
    assert nb.enqueue(7, date(2026, 9, 8),
                      "https://www.notion.so/24f1a2b3c4d54e6f8a9b0c1d2e3f4a5b",
                      "본문", "2026-09-08 출근 브리핑") is True
    sql, params = db.calls[0]
    assert "briefing_notion_outbox" in sql
    assert "briefing:2026-09-08" in params


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
