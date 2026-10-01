# tests/test_mail_sender_jobs.py
from unittest.mock import MagicMock

import pytest

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


def _deps(sent, g_values=None, send_side=None):
    g_values = g_values or {}
    return J.JobDeps(
        read_cell=lambda c, sid, sh, row, col: g_values.get(row, ""),
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
