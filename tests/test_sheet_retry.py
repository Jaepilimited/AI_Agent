# -*- coding: utf-8 -*-
"""배치가 구글 쪽 일시 장애로 **하루를 통째로 건너뛰지** 않는가.

⛔ 실제(2026-08-26·27): 제품 전성분 적재가 이틀 연속 실패했다.
       08-26  HttpError 503 (sheets.googleapis.com)
       08-27  TimeoutError: read operation timed out
   호출이 한 번뿐이라 그 순간에 걸리면 다음 시도가 24시간 뒤다 — 그 사이 성분
   데이터는 조용히 낡는다. 적재는 "실패"로 남지만 **답변은 아무 말 없이 옛 값을 쓴다.**
"""
import inspect
from pathlib import Path

import pytest

from app.core import retrying

ROOT = Path(__file__).resolve().parent.parent


class _Resp:
    def __init__(self, status):
        self.status = status


class _HttpError(Exception):
    def __init__(self, status):
        self.resp = _Resp(status)


@pytest.mark.parametrize("status,expected", [
    (503, True), (500, True), (502, True), (504, True), (429, True),
    (403, False), (404, False), (400, False),
])
def test_only_transient_statuses_are_retried(status, expected):
    """⚠️ 권한(403)·없는 시트(404)를 다시 걸면 기다리는 시간만 버리고 **진짜 원인을
       늦게 안다.** 다시 해서 달라질 수 있는 것만 다시 한다."""
    assert retrying.is_transient(_HttpError(status)) is expected


def test_socket_failures_are_retried():
    """`googleapiclient` 는 소켓 오류를 그대로 올려보낸다 — 타입이 여러 가지다."""
    assert retrying.is_transient(TimeoutError("read operation timed out"))
    assert retrying.is_transient(ConnectionResetError("reset"))
    assert not retrying.is_transient(ValueError("bad range"))


def test_retry_gives_up_and_raises_the_original():
    """⚠️ 재시도가 원인을 가리면 안 된다 — 마지막 예외를 그대로 올린다."""
    calls = []

    def always_fail():
        calls.append(1)
        raise TimeoutError("boom")

    with pytest.raises(TimeoutError):
        retrying.with_retry(always_fail, what="t", attempts=3, first_delay=0)
    assert len(calls) == 3


def test_permanent_failure_is_not_retried():
    calls = []

    def forbidden():
        calls.append(1)
        raise _HttpError(403)

    with pytest.raises(_HttpError):
        retrying.with_retry(forbidden, what="t", attempts=3, first_delay=0)
    assert len(calls) == 1, "권한 오류를 세 번 걸었다"


def test_sheet_readers_use_the_retry():
    """두 적재가 같은 노출을 갖는다 — 한쪽만 고치면 다른 쪽에서 같은 사고가 난다."""
    for module, fn in (("app/core/ingredients.py", "_read_sheet"),
                       ("app/core/inventory.py", "_fetch")):
        src = (ROOT / module).read_text(encoding="utf-8")
        body = src.split(f"def {fn}(", 1)[1].split("\ndef ", 1)[0]
        assert "with_retry" in body, module


def test_a_failed_day_gets_a_second_chance():
    """⛔ 04:00 에 실패하면 다음 시도가 24시간 뒤다. 늦게 한 번 더 걸되,
       그날 이미 성공했으면 **건너뛴다** — 성공한 날까지 두 번 부르면 없던 실패를
       만들 여지만 늘린다."""
    src = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
    # ⚠️ 호출이 두 줄로 나뉘어 있다 — 한 줄로 이어 붙여 본다
    stmt = src.split("_scheduler.add_job(_ingredient_sync_job", 1)[1].split(")", 1)[0]
    assert 'hour="4,6"' in stmt, stmt.strip()

    guard = src.split("def _ingredients_loaded_today(", 1)[1].split("\nasync def ", 1)[0]
    # ⚠️ 테이블 행 수가 아니라 **실행 기록**으로 판정해야 한다 (어제 것이 남아 있다)
    assert "job_runs" in guard and "ok = 1" in guard
    assert "CURDATE()" in guard
    # 판정을 못 하면 돌린다 — 건너뛰는 쪽으로 실패하면 그날이 빈다
    assert "return False" in guard.split("except", 1)[1]
