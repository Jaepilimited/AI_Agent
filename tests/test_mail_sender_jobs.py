# tests/test_mail_sender_jobs.py
import re
import socket
import threading
import time as _time
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

import httplib2
import pytest
import requests
from google.auth.exceptions import RefreshError
from googleapiclient.errors import HttpError

from app.core import mail_sender_jobs as J
from app.core.mail_sender import Columns, Row
from app.core.mail_template import MailTemplate

COLS = Columns(email=3, manager=1, status=6, note=7, date=0, variables={"kol id": 2})
TPL = MailTemplate(subject="[S] {kol id}", html="<p>{kol id}</p>", images={})


@pytest.fixture
def db(monkeypatch):
    d = J.MemoryStore()
    monkeypatch.setattr(J, "_store", d)
    return d


def _rows(n):
    return [Row(row=i + 2, manager="강하니", email=f"u{i}@x.com", status="", values={"kol id": f"k{i}"}) for i in range(n)]


def _reader(g_values=None, emails=None):
    """시트 한 행의 E(이메일)·G(전송여부) — 기본 이메일은 _rows 와 같다 (u{row-2}@x.com)."""
    g_values, emails = g_values or {}, emails or {}

    def read(c, sid, sh, row, cols):
        vals = {COLS.status: g_values.get(row, ""), COLS.email: emails.get(row, f"u{row - 2}@x.com")}
        return {k: vals.get(k, "") for k in cols}
    return read


def _deps(sent, g_values=None, send_side=None, emails=None):
    return J.JobDeps(
        read_cells=_reader(g_values, emails),
        write_cells=MagicMock(),
        send_raw=send_side or (lambda c, raw: sent.append(raw) or f"id{len(sent)}"),
        sleep=lambda s: None)


def test_sends_all_and_marks_O(db):
    sent = []
    jid = J.create_job("me@x.com", "sid", "gmail_a", "me@x.com", _rows(3))
    deps = _deps(sent)
    J.run_job(jid, object(), COLS, {"강하니": TPL}, deps)
    job = J.get_job(jid, "me@x.com")
    assert (job["status"], job["sent"], job["failed"]) == ("done", 3, 0)
    assert len(sent) == 3
    assert deps.write_cells.call_args_list[0].args[3] == 2  # 행 번호
    assert deps.write_cells.call_args_list[0].args[4][COLS.status] == "O"


def test_row_marked_O_midway_is_skipped(db):
    sent = []
    jid = J.create_job("me@x.com", "sid", "gmail_a", "me@x.com", _rows(3))
    J.run_job(jid, object(), COLS, {"강하니": TPL}, _deps(sent, g_values={3: "O"}))
    job = J.get_job(jid, "me@x.com")
    assert (job["sent"], job["skipped"]) == (2, 1)


def test_same_error_three_times_stops_job(db):
    def boom(c, raw):
        raise RuntimeError("insufficientPermissions")
    jid = J.create_job("me@x.com", "sid", "gmail_a", "me@x.com", _rows(10))
    J.run_job(jid, object(), COLS, {"강하니": TPL}, _deps([], send_side=boom))
    job = J.get_job(jid, "me@x.com")
    assert job["status"] == "stopped" and job["failed"] == 3 and "insufficientPermissions" in job["stop_reason"]


def test_same_sheet_concurrent_job_rejected(db):
    J.create_job("me@x.com", "sid", "gmail_a", "me@x.com", _rows(1))
    with pytest.raises(RuntimeError, match="이미 발송 중"):
        J.create_job("you@x.com", "sid", "gmail_a", "you@x.com", _rows(1))


def test_restart_marks_interrupted_not_resumed(db):
    jid = J.create_job("me@x.com", "sid", "gmail_a", "me@x.com", _rows(2))
    db.set_item_status(jid, 2, "sending")
    J.recover_after_restart()
    job = J.get_job(jid, "me@x.com")
    assert job["status"] == "interrupted"
    assert db.item_status(jid, 2) == "unknown"


def test_stop_request(db):
    sent = []
    jid = J.create_job("me@x.com", "sid", "gmail_a", "me@x.com", _rows(5))
    deps = _deps(sent)
    deps.sleep = lambda s: J.request_stop(jid, "me@x.com")
    J.run_job(jid, object(), COLS, {"강하니": TPL}, deps)
    assert J.get_job(jid, "me@x.com")["status"] == "stopped" and len(sent) == 1


# ---------------------------------------------------------------------------
# 중복 발송 방지 보강 (리뷰 지적 사항)
# ---------------------------------------------------------------------------
def _http_error(status):
    resp = httplib2.Response({"status": status})
    resp.reason = "x"
    return HttpError(resp, b'{"error": {"message": "x"}}')


def _x_written(deps):
    return [c for c in deps.write_cells.call_args_list if c.args[4].get(COLS.status) == "X"]


def test_lowercase_o_is_skipped(db):
    sent = []
    jid = J.create_job("me@x.com", "sid", "gmail_a", "me@x.com", _rows(3))
    J.run_job(jid, object(), COLS, {"강하니": TPL}, _deps(sent, g_values={3: "o", 4: " O "}))
    job = J.get_job(jid, "me@x.com")
    assert (job["sent"], job["skipped"]) == (1, 2) and len(sent) == 1


@pytest.mark.parametrize("where", ["send", "read"])
def test_refresh_error_stops_job_and_returns_item_to_pending(db, where):
    sent = []
    calls = {"n": 0}

    def send(c, raw):
        calls["n"] += 1
        if where == "send" and calls["n"] == 2:
            raise RefreshError("invalid_grant")
        sent.append(raw)
        return "id"

    def read(c, sid, sh, row, cols):
        if where == "read" and row == 3:
            raise RefreshError("invalid_grant")
        return _reader()(c, sid, sh, row, cols)

    jid = J.create_job("me@x.com", "sid", "gmail_a", "me@x.com", _rows(5))
    deps = _deps(sent, send_side=send)
    deps.read_cells = read
    J.run_job(jid, object(), COLS, {"강하니": TPL}, deps)
    job = J.get_job(jid, "me@x.com")
    assert job["status"] == "stopped"
    assert job["stop_reason"] == "구글 연결이 만료되었습니다 — 다시 연결 후 이어서 보내세요"
    assert (job["sent"], job["failed"]) == (1, 0) and job["finished_at"] is not None
    assert db.item_status(jid, 3) == "pending" and db.item_status(jid, 4) == "pending"
    assert not _x_written(deps)


def test_permission_403_stops_without_X(db):
    def send(c, raw):
        raise _http_error(403)
    jid = J.create_job("me@x.com", "sid", "gmail_a", "me@x.com", _rows(4))
    deps = _deps([], send_side=send)
    J.run_job(jid, object(), COLS, {"강하니": TPL}, deps)
    job = J.get_job(jid, "me@x.com")
    assert job["status"] == "stopped" and "403" in job["stop_reason"] and job["failed"] == 0
    assert db.item_status(jid, 2) == "pending" and not _x_written(deps)


def test_db_failure_after_send_does_not_mark_failed(db):
    real = db.set_item_status

    def flaky(jid, row, status, error=None, message_id=None):
        if status == "sent":
            raise RuntimeError("DB 끊김")
        return real(jid, row, status, error=error, message_id=message_id)

    db.set_item_status = flaky
    sent = []
    jid = J.create_job("me@x.com", "sid", "gmail_a", "me@x.com", _rows(2))
    deps = _deps(sent)
    J.run_job(jid, object(), COLS, {"강하니": TPL}, deps)
    job = J.get_job(jid, "me@x.com")
    assert job["failed"] == 0 and job["sent"] == 2 and len(sent) == 2
    assert not _x_written(deps)
    assert [c.args[4][COLS.status] for c in deps.write_cells.call_args_list] == ["O", "O"]
    # DB 에 sent 를 못 남긴 행은 'sending' 으로 방치되지 않고 '불확실' 로 남는다 (재발송 금지)
    assert db.item_status(jid, 2) == "unknown" and job["status"] == "done"


def test_bump_failure_after_send_does_not_mark_failed(db):
    real = db.bump

    def flaky(jid, col, n=1):
        if col == "sent":
            raise RuntimeError("DB 끊김")
        return real(jid, col, n)

    db.bump = flaky
    jid = J.create_job("me@x.com", "sid", "gmail_a", "me@x.com", _rows(2))
    deps = _deps([])
    J.run_job(jid, object(), COLS, {"강하니": TPL}, deps)
    job = J.get_job(jid, "me@x.com")
    assert job["failed"] == 0 and job["status"] == "done" and not _x_written(deps)
    assert db.item_status(jid, 2) == "sent"


def test_sheet_write_failure_after_send_is_not_a_failure(db):
    sent = []
    jid = J.create_job("me@x.com", "sid", "gmail_a", "me@x.com", _rows(2))
    deps = _deps(sent)
    deps.write_cells = MagicMock(side_effect=[RuntimeError("시트 쓰기 실패"), None])
    J.run_job(jid, object(), COLS, {"강하니": TPL}, deps)
    job = J.get_job(jid, "me@x.com")
    assert (job["sent"], job["failed"], job["sheet_write_failed"]) == (2, 0, 1)
    assert db.item_status(jid, 2) == "sent" and not _x_written(deps)
    item = next(i for i in db.items[jid] if i["sheet_row"] == 2)
    assert "시트 기록 실패" in item["error"]


def test_sheet_write_failing_three_times_stops_job(db):
    sent = []
    jid = J.create_job("me@x.com", "sid", "gmail_a", "me@x.com", _rows(6))
    deps = _deps(sent)
    deps.write_cells = MagicMock(side_effect=RuntimeError("시트 쓰기 실패"))
    J.run_job(jid, object(), COLS, {"강하니": TPL}, deps)
    job = J.get_job(jid, "me@x.com")
    assert job["status"] == "stopped" and len(sent) == 3 and job["sheet_write_failed"] == 3
    assert job["failed"] == 0 and "시트" in job["stop_reason"]


@pytest.mark.parametrize("exc", [
    TimeoutError("t"), socket.timeout("t"), ConnectionError("c"), ConnectionResetError("r"),
    requests.exceptions.ConnectionError("c"), requests.exceptions.Timeout("t"),
    httplib2.HttpLib2Error("h"), _http_error(500), _http_error(503),
], ids=lambda e: type(e).__name__ + str(getattr(getattr(e, "resp", None), "status", "")))
def test_transport_uncertain_send_is_unknown_not_X(db, exc):
    calls = {"n": 0}

    def send(c, raw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise exc
        return "id"

    jid = J.create_job("me@x.com", "sid", "gmail_a", "me@x.com", _rows(2))
    deps = _deps([], send_side=send)
    J.run_job(jid, object(), COLS, {"강하니": TPL}, deps)
    job = J.get_job(jid, "me@x.com")
    assert (job["sent"], job["failed"], job["unknown"]) == (1, 0, 1)
    assert db.item_status(jid, 2) == "unknown"
    item = next(i for i in db.items[jid] if i["sheet_row"] == 2)
    assert item["error"].startswith("발송 여부 불확실 — 보낸편지함 확인")
    assert not _x_written(deps)
    notes = [c.args[4] for c in deps.write_cells.call_args_list if c.args[3] == 2]
    assert notes == [{COLS.note: "발송 여부 불확실 — 보낸편지함 확인"}]
    assert job["unknown_rows"] == [2]


def test_uncertain_three_in_a_row_stops(db):
    def send(c, raw):
        raise TimeoutError("timed out")
    jid = J.create_job("me@x.com", "sid", "gmail_a", "me@x.com", _rows(6))
    J.run_job(jid, object(), COLS, {"강하니": TPL}, _deps([], send_side=send))
    job = J.get_job(jid, "me@x.com")
    assert job["status"] == "stopped" and job["unknown"] == 3 and job["failed"] == 0


def test_http_4xx_is_a_plain_failure(db):
    def send(c, raw):
        raise _http_error(400)
    jid = J.create_job("me@x.com", "sid", "gmail_a", "me@x.com", _rows(1))
    deps = _deps([], send_side=send)
    J.run_job(jid, object(), COLS, {"강하니": TPL}, deps)
    assert J.get_job(jid, "me@x.com")["failed"] == 1 and len(_x_written(deps)) == 1


def test_streak_key_ignores_message_text(db):
    n = {"i": 0}

    def send(c, raw):
        n["i"] += 1
        raise RuntimeError(f"insufficientPermissions req-{n['i']}")
    jid = J.create_job("me@x.com", "sid", "gmail_a", "me@x.com", _rows(10))
    J.run_job(jid, object(), COLS, {"강하니": TPL}, _deps([], send_side=send))
    job = J.get_job(jid, "me@x.com")
    assert job["status"] == "stopped" and job["failed"] == 3


def test_streak_key_uses_http_status(db):
    codes = iter([400, 404, 400, 404])

    def send(c, raw):
        raise _http_error(next(codes))
    jid = J.create_job("me@x.com", "sid", "gmail_a", "me@x.com", _rows(4))
    J.run_job(jid, object(), COLS, {"강하니": TPL}, _deps([], send_side=send))
    job = J.get_job(jid, "me@x.com")
    assert job["status"] == "done" and job["failed"] == 4


def test_error_kind():
    assert J._error_kind(RuntimeError("a")) == J._error_kind(RuntimeError("b"))
    assert J._error_kind(_http_error(400)) != J._error_kind(_http_error(404))


def test_thread_main_crash_marks_stopped(db):
    jid = J.create_job("me@x.com", "sid", "gmail_a", "me@x.com", _rows(2))

    def boom(_jid):
        raise ValueError("boom")
    db.pending_items = boom
    J._thread_main(jid, object(), COLS, {"강하니": TPL}, _deps([]))
    job = J.get_job(jid, "me@x.com")
    assert job["status"] == "stopped" and job["stop_reason"] == "내부 오류: boom"
    assert job["finished_at"] is not None


def test_thread_main_crash_mid_send_leaves_no_sending_item(db):
    jid = J.create_job("me@x.com", "sid", "gmail_a", "me@x.com", _rows(2))
    real_job = db.job
    state = {"n": 0}

    def flaky_job(j):  # 발송 뒤 루프 첫머리의 상태 조회에서 터진다
        state["n"] += 1
        if state["n"] == 3:
            raise ValueError("db gone")
        return real_job(j)
    db.job = flaky_job
    real_set = db.set_item_status

    def no_sent(jid_, row, status, error=None, message_id=None):
        if status == "sent":
            raise RuntimeError("DB 끊김")
        return real_set(jid_, row, status, error=error, message_id=message_id)
    db.set_item_status = no_sent
    J._thread_main(jid, object(), COLS, {"강하니": TPL}, _deps([]))
    db.job = real_job
    job = J.get_job(jid, "me@x.com")
    assert job["status"] == "stopped" and job["stop_reason"] == "내부 오류: db gone"
    assert "sending" not in [i["status"] for i in db.items[jid]]
    assert db.item_status(jid, 2) == "unknown"


def test_start_job_thread_targets_thread_main(monkeypatch):
    seen = {}

    class FakeThread:
        def __init__(self, target=None, args=(), daemon=None, name=None):
            seen.update(target=target, daemon=daemon)

        def start(self):
            seen["started"] = True
    monkeypatch.setattr(J.threading, "Thread", FakeThread)
    J.start_job_thread(1, object(), COLS, {})
    assert seen == {"target": J._thread_main, "daemon": True, "started": True}


def test_missing_template_is_clear_error(db):
    jid = J.create_job("me@x.com", "sid", "gmail_a", "me@x.com", _rows(1))
    J.run_job(jid, object(), COLS, {}, _deps([]))
    item = db.items[jid][0]
    assert item["status"] == "failed" and item["error"] == "템플릿 없음: 강하니"


def test_daily_limit_reached_mid_job_stops(db, monkeypatch):
    monkeypatch.setattr(db, "sent_today", lambda owner: J.DAILY_LIMIT)
    sent = []
    jid = J.create_job("me@x.com", "sid", "gmail_a", "me@x.com", _rows(2))
    J.run_job(jid, object(), COLS, {"강하니": TPL}, _deps(sent))
    job = J.get_job(jid, "me@x.com")
    assert job["status"] == "stopped" and not sent and "한도" in job["stop_reason"]
    assert db.item_status(jid, 2) == "pending"


def test_memory_store_concurrent_create_exactly_one(monkeypatch):
    class Slow(J.MemoryStore):
        def active_for_sheet(self, sid, sheet):
            r = super().active_for_sheet(sid, sheet)
            _time.sleep(0.05)
            return r
    monkeypatch.setattr(J, "_store", Slow())
    ok, err = [], []

    def go(owner):
        try:
            ok.append(J.create_job(owner, "sid", "gmail_a", owner, _rows(1)))
        except RuntimeError as e:
            err.append(str(e))
    ts = [threading.Thread(target=go, args=(o,)) for o in ("a@x.com", "b@x.com")]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert len(ok) == 1 and len(err) == 1 and "이미 발송 중" in err[0]


def test_store_whitelists():
    s = J.MemoryStore()
    jid = s.insert_job("me", "sid", "sh", "me", _rows(1))
    for col in ("sent", "failed", "skipped", "unknown", "sheet_write_failed"):
        s.bump(jid, col)
    with pytest.raises(AssertionError):
        s.bump(jid, "owner")
    s.set_job(jid, status="done", stop_reason="x", finished_at=datetime.now())
    with pytest.raises(AssertionError):
        s.set_job(jid, owner="evil")
    m = J.MariaStore.__new__(J.MariaStore)
    m.db = MagicMock()
    with pytest.raises(AssertionError):
        m.bump(1, "id=0, owner")
    with pytest.raises(AssertionError):
        m.set_job(1, owner="x")
    m.bump(1, "unknown", 2)
    m.set_job(1, status="done")
    assert m.db.execute.call_count == 2


def test_memory_interrupt_all_sets_finished_at_and_unknown(db):
    jid = J.create_job("me@x.com", "sid", "gmail_a", "me@x.com", _rows(2))
    db.set_item_status(jid, 2, "sending")
    J.recover_after_restart()
    job = J.get_job(jid, "me@x.com")
    assert job["finished_at"] is not None and job["unknown"] == 1 and job["unknown_rows"] == [2]


def test_unknown_rows_and_ack(db):
    jid = J.create_job("me@x.com", "sid", "gmail_a", "me@x.com", _rows(3))
    db.set_item_status(jid, 3, "sending")
    db.set_item_status(jid, 4, "sending")
    J.recover_after_restart()
    assert sorted(h["sheet_row"] for h in J.sheet_history("sid", "gmail_a")) == [3, 4]
    assert J.sheet_history("sid", "other") == []
    assert J.ack_unknown(jid, "you@x.com") is None  # 남의 작업
    assert J.ack_unknown(jid, "me@x.com") == 2
    assert db.item_status(jid, 3) == "acknowledged"
    assert J.sheet_history("sid", "gmail_a") == [] and J.get_job(jid, "me@x.com")["unknown_rows"] == []


class _FakeCursor:
    def __init__(self, log, active=None, blocked=()):
        self.log, self.active, self.blocked = log, active, list(blocked)
        self.lastrowid = 42
        self._last = ""

    def fetchall(self):
        if "FROM mail_send_items" in self._last:
            return [{"job_id": 9, "owner": "o", "sheet_row": r, "email": f"u{r - 2}@x.com", "status": "sent",
                     "job_status": "done"} for r in self.blocked]
        return []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=()):
        self.log.append(("execute", sql, params))
        self._last = sql

    def executemany(self, sql, seq):
        self.log.append(("executemany", sql, list(seq)))

    def fetchone(self):
        if "GET_LOCK" in self._last:
            return {"got": 1}
        if "FROM mail_send_jobs" in self._last:
            return self.active
        return None


class _FakeConn:
    def __init__(self, cur):
        self.cur, self.log = cur, cur.log

    def cursor(self):
        return self.cur

    def commit(self):
        self.log.append(("commit",))

    def rollback(self):
        self.log.append(("rollback",))

    def close(self):
        self.log.append(("close",))


def _maria(cur):
    m = J.MariaStore.__new__(J.MariaStore)
    m.db = MagicMock()
    m.db.get_maria_conn.return_value = _FakeConn(cur)
    return m


def test_maria_insert_job_one_connection_lock_executemany_single_commit():
    log = []
    m = _maria(_FakeCursor(log))
    assert m.insert_job("me", "sid", "gmail_a", "me", _rows(3)) == 42
    m.db.get_maria_conn.assert_called_once()
    sqls = [e[1] for e in log if e[0] in ("execute", "executemany")]
    assert "GET_LOCK" in sqls[0] and "RELEASE_LOCK" in sqls[-1]
    lock_name = log[0][2][0]
    assert lock_name.startswith("mail_sheet:") and len(lock_name) <= 64
    many = [e for e in log if e[0] == "executemany"]
    assert len(many) == 1 and len(many[0][2]) == 3
    commits = [i for i, e in enumerate(log) if e[0] == "commit"]
    assert commits[-1] > log.index(many[0])
    assert ("rollback",) not in log and log[-1] == ("close",)
    assert m.db.execute.call_count == 0 and m.db.execute_lastid.call_count == 0


def test_maria_insert_job_refuses_active_sheet_and_rolls_back_on_error():
    log = []
    m = _maria(_FakeCursor(log, active={"id": 1}))
    with pytest.raises(RuntimeError, match="이미 발송 중"):
        m.insert_job("me", "sid", "gmail_a", "me", _rows(1))
    assert not [e for e in log if e[0] == "executemany"]
    assert any("RELEASE_LOCK" in e[1] for e in log if e[0] == "execute")
    log2 = []
    cur2 = _FakeCursor(log2)
    cur2.executemany = MagicMock(side_effect=RuntimeError("insert failed"))
    m2 = _maria(cur2)
    with pytest.raises(RuntimeError, match="insert failed"):
        m2.insert_job("me", "sid", "gmail_a", "me", _rows(1))
    assert ("rollback",) in log2 and any("RELEASE_LOCK" in e[1] for e in log2 if e[0] == "execute")
    assert log2[-1] == ("close",)


def test_ensure_tables_adds_new_counter_columns(monkeypatch, db):
    import app.db.mariadb as M
    ran = []
    monkeypatch.setattr(M, "execute", lambda sql, params=(): ran.append(sql) or 0)
    J.ensure_mail_sender_tables()
    joined = "\n".join(ran)
    assert "sheet_write_failed" in ran[0] and "`unknown`" in ran[0]
    for col in ("sheet_write_failed", "`unknown`"):
        assert re.search(rf"ALTER TABLE mail_send_jobs ADD COLUMN IF NOT EXISTS {col}", joined), col


def test_no_unused_field_import():
    src = Path(J.__file__).read_text(encoding="utf-8")
    assert "dataclass, field" not in src


# ---------------------------------------------------------------------------
# Fix Round 1 (after redo)
# ---------------------------------------------------------------------------
def _seed_history(store, row, status, owner="other@x.com", sheet="gmail_a", email=None, job_status="done"):
    """같은 시트의 끝난 앞선 작업 — 그 행의 발송 기록 상태를 남긴다."""
    jid = max(store.jobs, default=0) + 1
    store.jobs[jid] = dict(id=jid, owner=owner, spreadsheet_id="sid", sheet=sheet, sender=owner, status=job_status,
                           total=1, sent=0, failed=0, skipped=0, sheet_write_failed=0, unknown=0,
                           stop_reason=None, created_at=datetime.now(), finished_at=datetime.now())
    store.items[jid] = [dict(sheet_row=row, email=email or f"h{row}@x.com", manager="강하니", row_values={},
                             status=status, error=None, message_id=None, sent_at=None)]
    return jid


def test_sheet_history_statuses(db):
    for row, st in [(2, "sent"), (3, "sending"), (4, "unknown"), (5, "acknowledged"), (6, "failed"),
                    (7, "skipped"), (8, "pending")]:
        _seed_history(db, row, st)
    _seed_history(db, 9, "sent", sheet="gmail_b")
    hist = J.sheet_history("sid", "gmail_a")
    assert sorted((h["sheet_row"], h["status"]) for h in hist) == [(2, "sent"), (3, "sending"), (4, "unknown")]
    assert {"job_id", "owner", "email", "job_status"} <= set(hist[0])
    assert [h["sheet_row"] for h in db.history("sid", "gmail_a", row=3, email="nobody@x.com")] == [3]
    assert [h["sheet_row"] for h in db.history("sid", "gmail_a", row=99, email=" H4@X.com ")] == [4]
    jid = _seed_history(db, 10, "sent")
    assert 10 not in [h["sheet_row"] for h in db.history("sid", "gmail_a", exclude_job=jid)]


def test_insert_job_drops_blocked_rows_under_lock(db):
    _seed_history(db, 3, "unknown")
    jid = J.create_job("me@x.com", "sid", "gmail_a", "me@x.com", _rows(3), confirm_count=2)
    assert [i["sheet_row"] for i in db.items[jid]] == [2, 4] and db.jobs[jid]["total"] == 2


def test_insert_job_count_changed_is_refused(db):
    _seed_history(db, 3, "unknown")  # 점검 뒤 시작 전에 생긴 불확실 행
    with pytest.raises(RuntimeError, match="대상이 바뀌었습니다"):
        J.create_job("me@x.com", "sid", "gmail_a", "me@x.com", _rows(3), confirm_count=3)
    assert len(db.jobs) == 1  # 새 작업은 만들어지지 않았다


def test_run_job_skips_row_with_db_history(db):
    sent = []
    jid = J.create_job("me@x.com", "sid", "gmail_a", "me@x.com", _rows(2))
    _seed_history(db, 9, "sent", email="U0@x.com ")  # 정렬로 다른 행에 있던, O 를 못 남긴 앞선 발송
    deps = _deps(sent)
    J.run_job(jid, object(), COLS, {"강하니": TPL}, deps)
    job = J.get_job(jid, "me@x.com")
    assert len(sent) == 1 and (job["sent"], job["skipped"]) == (1, 1)
    item = next(i for i in db.items[jid] if i["sheet_row"] == 2)
    assert item["status"] == "skipped" and item["error"].startswith("발송 기록 있음")
    assert "이메일" in item["error"]
    assert [c.args[3] for c in deps.write_cells.call_args_list] == [3]  # 2행 시트는 건드리지 않는다


def test_run_job_sends_new_recipient_on_old_sent_row(db):
    sent = []
    jid = J.create_job("me@x.com", "sid", "gmail_a", "me@x.com", _rows(1))
    _seed_history(db, 2, "sent", email="someone-else@x.com")  # 같은 행 번호, 다른 사람
    J.run_job(jid, object(), COLS, {"강하니": TPL}, _deps(sent))
    assert len(sent) == 1


def test_run_job_unknown_blocks_by_row_or_email(db):
    sent = []
    jid = J.create_job("me@x.com", "sid", "gmail_a", "me@x.com", _rows(3))
    _seed_history(db, 2, "unknown", email="x@x.com")      # 행이 같다
    _seed_history(db, 50, "sending", email="u1@x.com")    # 이메일이 같다
    J.run_job(jid, object(), COLS, {"강하니": TPL}, _deps(sent))
    st = {i["sheet_row"]: (i["status"], i["error"]) for i in db.items[jid]}
    assert len(sent) == 1 and st[4][0] == "sent"
    assert st[2][0] == "skipped" and "행" in st[2][1] and st[3][0] == "skipped" and "이메일" in st[3][1]


def test_blocking_record_rules():
    hist = [dict(job_id=1, owner="a", sheet_row=3, email="p@x.com", status="sent", job_status="done"),
            dict(job_id=2, owner="b", sheet_row=5, email="q@x.com", status="unknown", job_status="interrupted")]
    assert J.blocking_record(hist, 3, "new@x.com") is None                    # sent: 행만 같음 → 다른 사람
    assert J.blocking_record(hist, 8, " P@X.com")["by"] == "이메일"            # sent: 이메일이 같음
    assert J.blocking_record(hist, 5, "z@x.com")["by"] == "행"                 # unknown: 행
    assert J.blocking_record(hist, 9, "q@x.com")["by"] == "이메일"             # unknown: 이메일
    assert J.blocking_record(hist, 9, "z@x.com") is None


def test_ack_resolves_orphaned_sending_of_non_running_job(db):
    jid = _seed_history(db, 3, "sending", owner="me@x.com", job_status="stopped")
    assert J.ack_unknown(jid, "me@x.com") == 1 and db.item_status(jid, 3) == "acknowledged"
    live = _seed_history(db, 4, "sending", owner="me@x.com", job_status="running")
    assert J.ack_unknown(live, "me@x.com") == 0 and db.item_status(live, 4) == "sending"


def test_own_unknown_jobs():
    hist = [dict(job_id=1, owner="me", sheet_row=3, email="a", status="unknown", job_status="interrupted"),
            dict(job_id=1, owner="me", sheet_row=2, email="b", status="sending", job_status="stopped"),
            dict(job_id=2, owner="me", sheet_row=4, email="c", status="sending", job_status="running"),
            dict(job_id=3, owner="you", sheet_row=5, email="d", status="unknown", job_status="done"),
            dict(job_id=4, owner="me", sheet_row=6, email="e", status="sent", job_status="done")]
    assert J.own_unknown_jobs(hist, "me") == [{"job_id": 1, "rows": [2, 3]}]


@pytest.mark.parametrize("exc", [_http_error(500), TimeoutError("t"), RuntimeError("x")],
                         ids=["5xx", "timeout", "runtime"])
def test_read_failure_writes_note_only_never_G(db, exc):
    def read(c, sid, sh, row, cols):
        raise exc
    jid = J.create_job("me@x.com", "sid", "gmail_a", "me@x.com", _rows(1))
    deps = _deps([])
    deps.read_cells = read
    J.run_job(jid, object(), COLS, {"강하니": TPL}, deps)
    assert db.items[jid][0]["status"] == "failed"
    assert db.items[jid][0]["error"].startswith("시트 확인 실패 — 다시 시도")
    assert [c.args[4] for c in deps.write_cells.call_args_list] == [{COLS.note: "시트 확인 실패 — 다시 시도"}]


@pytest.mark.parametrize("status", ["interrupted", "stopped", "done"])
def test_run_job_exits_when_not_running(db, status):
    sent = []
    jid = J.create_job("me@x.com", "sid", "gmail_a", "me@x.com", _rows(3))
    db.jobs[jid]["status"] = status  # 다른 프로세스가 바꿨다
    J.run_job(jid, object(), COLS, {"강하니": TPL}, _deps(sent))
    assert not sent and db.jobs[jid]["status"] == status


def test_maria_insert_job_drops_blocked_rows_and_checks_count():
    log = []
    m = _maria(_FakeCursor(log, blocked=[3]))
    assert m.insert_job("me", "sid", "gmail_a", "me", _rows(3), confirm_count=2) == 42
    many = [e for e in log if e[0] == "executemany"][0]
    assert [p[1] for p in many[2]] == [2, 4]
    ins = next(e for e in log if e[0] == "execute" and "INSERT INTO mail_send_jobs" in e[1])
    assert ins[2][-1] == 2  # total
    blocked_q = next(i for i, e in enumerate(log) if e[0] == "execute" and "FROM mail_send_items" in e[1])
    lock_q = next(i for i, e in enumerate(log) if e[0] == "execute" and "GET_LOCK" in e[1])
    assert lock_q < blocked_q < log.index(ins)  # 잠금 안에서 다시 판단
    log2 = []
    m2 = _maria(_FakeCursor(log2, blocked=[3]))
    with pytest.raises(RuntimeError, match="대상이 바뀌었습니다"):
        m2.insert_job("me", "sid", "gmail_a", "me", _rows(3), confirm_count=3)
    assert not [e for e in log2 if e[0] == "executemany"] and ("rollback",) in log2
    assert any("RELEASE_LOCK" in e[1] for e in log2 if e[0] == "execute")


def test_maria_history_and_ack_sql():
    m = J.MariaStore.__new__(J.MariaStore)
    m.db = MagicMock()
    m.db.fetch_all.return_value = []
    m.history("sid", "sh", exclude_job=5, row=3, email=" A@x.com")
    sql, params = m.db.fetch_all.call_args.args
    assert "LOWER(TRIM(i.email))" in sql and "i.job_id<>%s" in sql and "i.sheet_row=%s OR" in sql
    assert params == ("sid", "sh", 5, 3, "a@x.com")
    m.db.execute.return_value = 2
    assert m.ack_unknown(7) == 2
    sql = m.db.execute.call_args.args[0]
    assert "i.status='sending'" in sql and "NOT IN ('running','stopping')" in sql


def test_insert_job_drops_sent_email_under_lock(db):
    _seed_history(db, 30, "sent", email="u1@x.com")  # 정렬로 30행에 있던 사람이 지금 3행
    jid = J.create_job("me@x.com", "sid", "gmail_a", "me@x.com", _rows(3), confirm_count=2)
    assert [i["sheet_row"] for i in db.items[jid]] == [2, 4]


# ---------------------------------------------------------------------------
# Final review — 발송 중 행 이동, 시트 429 재시도
# ---------------------------------------------------------------------------
def test_row_shifted_before_send_is_skipped_without_sheet_write(db):
    sent = []
    jid = J.create_job("me@x.com", "sid", "gmail_a", "me@x.com", _rows(2))
    deps = _deps(sent, emails={2: "Someone-Else@x.com"})  # 정렬로 2행에 다른 사람이 들어왔다
    J.run_job(jid, object(), COLS, {"강하니": TPL}, deps)
    item = next(i for i in db.items[jid] if i["sheet_row"] == 2)
    assert item["status"] == "failed" and item["error"] == "시트 행이 바뀜 — 다시 점검"
    assert len(sent) == 1  # 3행만 나갔다
    assert [c.args[3] for c in deps.write_cells.call_args_list] == [3]  # 2행엔 아무것도 쓰지 않는다


def test_row_email_compare_ignores_case_and_spaces(db):
    sent = []
    jid = J.create_job("me@x.com", "sid", "gmail_a", "me@x.com", _rows(1))
    J.run_job(jid, object(), COLS, {"강하니": TPL}, _deps(sent, emails={2: "  U0@X.com "}))
    assert len(sent) == 1 and db.items[jid][0]["status"] == "sent"


def test_row_shifted_between_send_and_O_write(db):
    sent = []
    reads = {"n": 0}
    base = _reader()

    def read(c, sid, sh, row, cols):
        reads["n"] += 1
        if reads["n"] == 2:  # 발송 뒤 O 쓰기 전 재확인 — 그사이 정렬됐다
            return {k: ("moved@x.com" if k == COLS.email else "") for k in cols}
        return base(c, sid, sh, row, cols)
    jid = J.create_job("me@x.com", "sid", "gmail_a", "me@x.com", _rows(1))
    deps = _deps(sent)
    deps.read_cells = read
    J.run_job(jid, object(), COLS, {"강하니": TPL}, deps)
    job = J.get_job(jid, "me@x.com")
    assert len(sent) == 1 and (job["sent"], job["sheet_write_failed"], job["failed"]) == (1, 1, 0)
    assert deps.write_cells.call_count == 0  # O 를 남의 행에 쓰지 않는다
    item = db.items[jid][0]
    assert item["status"] == "sent" and item["error"] == "시트 기록 실패: 행이 바뀜"


def test_failure_X_is_not_written_on_shifted_row(db):
    reads = {"n": 0}
    base = _reader()

    def read(c, sid, sh, row, cols):
        reads["n"] += 1
        if reads["n"] == 2:
            return {k: ("moved@x.com" if k == COLS.email else "O") for k in cols}
        return base(c, sid, sh, row, cols)

    def send(c, raw):
        raise _http_error(400)
    jid = J.create_job("me@x.com", "sid", "gmail_a", "me@x.com", _rows(1))
    deps = _deps([], send_side=send)
    deps.read_cells = read
    J.run_job(jid, object(), COLS, {"강하니": TPL}, deps)
    assert db.items[jid][0]["status"] == "failed" and not _x_written(deps)


def test_read_cells_reads_one_range():
    from app.core import mail_google
    svc = MagicMock()
    get = svc.spreadsheets.return_value.values.return_value.get
    get.return_value.execute.return_value = {"values": [[" a@x.com ", "", "", "O"]]}
    from unittest.mock import patch
    with patch("app.core.mail_google._sheets", return_value=svc):
        got = mail_google.read_cells(MagicMock(), "id", "Bob's", 5, [3, 6, 7])
    assert get.call_count == 1 and get.call_args.kwargs["range"] == "'Bob''s'!D5:H5"
    assert got == {3: "a@x.com", 6: "O", 7: ""}


def _flaky(times, exc_factory, then):
    n = {"i": 0}

    def fn(*a):
        n["i"] += 1
        if times is None or n["i"] <= times:
            raise exc_factory()
        return then(*a)
    return fn


def test_sheets_429_is_retried_then_succeeds(db):
    sent, slept = [], []
    jid = J.create_job("me@x.com", "sid", "gmail_a", "me@x.com", _rows(1))
    deps = _deps(sent)
    deps.read_cells = _flaky(1, lambda: _http_error(429), _reader())
    deps.write_cells = MagicMock(side_effect=[_http_error(503), None])
    deps.sleep = slept.append
    J.run_job(jid, object(), COLS, {"강하니": TPL}, deps)
    job = J.get_job(jid, "me@x.com")
    assert (job["status"], job["sent"], job["sheet_write_failed"]) == ("done", 1, 0)
    assert slept[:1] == [2] and slept.count(2) == 2  # 읽기 1회·쓰기 1회 재시도
    assert deps.write_cells.call_args_list[-1].args[4][COLS.status] == "O"


def test_sheets_429_always_stops_with_sheets_message(db):
    slept = []
    jid = J.create_job("me@x.com", "sid", "gmail_a", "me@x.com", _rows(2))
    deps = _deps([])
    deps.read_cells = _flaky(None, lambda: _http_error(429), _reader())
    deps.sleep = slept.append
    J.run_job(jid, object(), COLS, {"강하니": TPL}, deps)
    job = J.get_job(jid, "me@x.com")
    assert job["status"] == "stopped" and job["stop_reason"].startswith("구글 시트 사용량 한도(429)")
    assert slept == [2, 4, 8] and db.item_status(jid, 2) == "pending" and deps.write_cells.call_count == 0


def test_gmail_429_message_is_send_limit(db):
    def send(c, raw):
        raise _http_error(429)
    jid = J.create_job("me@x.com", "sid", "gmail_a", "me@x.com", _rows(1))
    J.run_job(jid, object(), COLS, {"강하니": TPL}, _deps([], send_side=send))
    job = J.get_job(jid, "me@x.com")
    assert job["status"] == "stopped" and job["stop_reason"].startswith("구글 발송 속도·한도 제한(429)")
    assert db.item_status(jid, 2) == "pending"


def test_sheet_O_write_429_always_stops_with_sheets_message(db):
    sent = []
    jid = J.create_job("me@x.com", "sid", "gmail_a", "me@x.com", _rows(3))
    deps = _deps(sent)
    deps.write_cells = MagicMock(side_effect=_http_error(429))
    J.run_job(jid, object(), COLS, {"강하니": TPL}, deps)
    job = J.get_job(jid, "me@x.com")
    assert len(sent) == 1 and job["sheet_write_failed"] == 1 and job["status"] == "stopped"
    assert job["stop_reason"].startswith("구글 시트 사용량 한도(429)")
    assert deps.write_cells.call_count == 4  # 1 + 재시도 3



def test_run_job_from_uses_sendas_display_name(db):
    from email import message_from_bytes, policy
    sent = []
    jid = J.create_job("me@x.com", "sid", "gmail_a", "mk@x.com", _rows(1))
    J.run_job(jid, object(), COLS, {"강하니": TPL}, _deps(sent), sender_name="마케팅팀")
    frm = message_from_bytes(sent[0], policy=policy.default)["From"]
    assert frm.addresses[0].display_name == "마케팅팀" and frm.addresses[0].addr_spec == "mk@x.com"


def test_start_job_thread_passes_sender_name(monkeypatch):
    seen = {}

    class FakeThread:
        def __init__(self, target=None, args=(), kwargs=None, daemon=None, name=None):
            seen.update(args=args, kwargs=kwargs or {})

        def start(self):
            pass
    monkeypatch.setattr(J.threading, "Thread", FakeThread)
    J.start_job_thread(1, "C", COLS, {}, sender_name="마케팅")
    assert seen["args"][:4] == (1, "C", COLS, {}) and "마케팅" in (*seen["args"], *seen["kwargs"].values())



def test_recovery_runs_lazily_once_after_boot_failure(monkeypatch):
    """부팅 때 DB 가 죽어 '중단됨' 정리를 못 했으면, 저장소를 처음 쓸 때 한 번 한다."""
    import app.db.mariadb as M
    monkeypatch.setattr(M, "execute", lambda sql, params=(): 0)

    class Flaky(J.MemoryStore):
        calls = 0

        def interrupt_all(self):
            self.calls += 1
            if self.calls <= 2:
                raise RuntimeError("db down")
            super().interrupt_all()
    st = Flaky()
    jid = st.insert_job("me@x.com", "sid", "gmail_a", "me@x.com", _rows(1))  # 재시작 전 발송 중이던 작업
    st.set_item_status(jid, 2, "sending")
    monkeypatch.setattr(J, "_store", st)
    monkeypatch.setattr(J, "_recovery_pending", False)
    with pytest.raises(RuntimeError):
        J.ensure_mail_sender_tables()
    assert J._recovery_pending is True
    with pytest.raises(RuntimeError):  # 아직 DB 가 죽어 있다 — 다음 사용에서 다시
        J.sent_today("me@x.com")
    assert J._recovery_pending is True
    assert J.sent_today("me@x.com") == 0
    assert st.calls == 3 and J._recovery_pending is False
    assert st.jobs[jid]["status"] == "interrupted" and st.item_status(jid, 2) == "unknown"
    J.active_job("me@x.com")
    assert st.calls == 3  # 한 번만


def test_no_lazy_recovery_when_boot_succeeded(monkeypatch, db):
    import app.db.mariadb as M
    monkeypatch.setattr(M, "execute", lambda sql, params=(): 0)
    J.ensure_mail_sender_tables()
    assert J._recovery_pending is False
    jid = J.create_job("me@x.com", "sid", "gmail_a", "me@x.com", _rows(1))
    assert J.get_job(jid, "me@x.com")["status"] == "running"  # 이 프로세스가 만든 작업은 건드리지 않는다


def test_maria_insert_job_discards_connection_when_release_fails():
    log = []
    cur = _FakeCursor(log)
    real = cur.execute

    def execute(sql, params=()):
        if "RELEASE_LOCK" in sql:
            raise RuntimeError("connection lost")
        return real(sql, params)
    cur.execute = execute
    m = _maria(cur)
    conn = m.db.get_maria_conn.return_value
    conn._con = MagicMock()  # PooledDB 의 steady 연결 — 그 안의 _con 이 실제 DB 세션
    assert m.insert_job("me", "sid", "gmail_a", "me", _rows(1)) == 42
    conn._con._con.close.assert_called_once()  # 잠금을 쥔 세션째 버린다
    assert log[-1] == ("close",)


def test_maria_insert_job_keeps_connection_when_release_ok():
    log = []
    m = _maria(_FakeCursor(log))
    conn = m.db.get_maria_conn.return_value
    conn._con = MagicMock()
    m.insert_job("me", "sid", "gmail_a", "me", _rows(1))
    conn._con._con.close.assert_not_called()
