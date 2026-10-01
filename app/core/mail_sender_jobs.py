# app/core/mail_sender_jobs.py
"""메일 발송 작업 — 기록은 MariaDB, 실행은 데몬 스레드.

⛔ 재시작 후 자동 재개하지 않는다: 'sending' 이던 행이 실제로 나갔는지 서버는 모른다.
⛔ 같은 원인 실패가 MAX_SAME_ERROR 번 연속이면 멈춘다 — 권한 문제로 전 행 X 가 되는 것을 막는다.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

from app.core import mail_google
from app.core.mail_mime import build_mime
from app.core.mail_sender import Columns, Row
from app.core.mail_template import render

MAX_SAME_ERROR = 3
SEND_DELAY = 1.0
_ACTIVE = ("running", "stopping")

_DDL = [
    """CREATE TABLE IF NOT EXISTS mail_send_jobs (
        id INT AUTO_INCREMENT PRIMARY KEY, owner VARCHAR(255) NOT NULL, spreadsheet_id VARCHAR(128) NOT NULL,
        sheet VARCHAR(255) NOT NULL, sender VARCHAR(255) NOT NULL, status VARCHAR(16) NOT NULL,
        total INT NOT NULL DEFAULT 0, sent INT NOT NULL DEFAULT 0, failed INT NOT NULL DEFAULT 0,
        skipped INT NOT NULL DEFAULT 0, stop_reason TEXT NULL,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, finished_at DATETIME NULL,
        INDEX idx_owner (owner, created_at), INDEX idx_sheet (spreadsheet_id, sheet, status)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS mail_send_items (
        id INT AUTO_INCREMENT PRIMARY KEY, job_id INT NOT NULL, sheet_row INT NOT NULL,
        email VARCHAR(320) NOT NULL, manager VARCHAR(255) NOT NULL, row_values JSON NOT NULL,
        status VARCHAR(16) NOT NULL DEFAULT 'pending', error TEXT NULL, message_id VARCHAR(64) NULL,
        sent_at DATETIME NULL, UNIQUE KEY uq_job_row (job_id, sheet_row), INDEX idx_sent (status, sent_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
]


class MemoryStore:
    """테스트용 저장소 — MariaStore 와 같은 메서드."""
    def __init__(self):
        self.jobs: dict[int, dict] = {}
        self.items: dict[int, list[dict]] = {}
        self._lock = threading.Lock()

    def active_for_sheet(self, sid, sheet):
        return any(j["spreadsheet_id"] == sid and j["sheet"] == sheet and j["status"] in _ACTIVE
                   for j in self.jobs.values())

    def insert_job(self, owner, sid, sheet, sender, rows):
        with self._lock:
            jid = len(self.jobs) + 1
            self.jobs[jid] = dict(id=jid, owner=owner, spreadsheet_id=sid, sheet=sheet, sender=sender,
                                  status="running", total=len(rows), sent=0, failed=0, skipped=0, stop_reason=None)
            self.items[jid] = [dict(sheet_row=r.row, email=r.email, manager=r.manager, row_values=dict(r.values),
                                    status="pending", error=None, message_id=None, sent_at=None) for r in rows]
            return jid

    def job(self, jid):
        j = self.jobs.get(jid)
        return dict(j) if j else None

    def pending_items(self, jid):
        return [dict(i) for i in self.items[jid] if i["status"] == "pending"]

    def set_item_status(self, jid, row, status, error=None, message_id=None):
        for i in self.items[jid]:
            if i["sheet_row"] == row:
                i.update(status=status, error=error, message_id=message_id,
                         sent_at=datetime.now() if status == "sent" else i["sent_at"])

    def item_status(self, jid, row):
        return next(i["status"] for i in self.items[jid] if i["sheet_row"] == row)

    def bump(self, jid, col):
        self.jobs[jid][col] += 1

    def set_job(self, jid, **kw):
        self.jobs[jid].update(kw)

    def interrupt_all(self):
        for jid, j in self.jobs.items():
            if j["status"] in _ACTIVE:
                j["status"] = "interrupted"
                for i in self.items[jid]:
                    if i["status"] == "sending":
                        i["status"] = "unknown"

    def sent_today(self, owner):
        today = datetime.now().date()
        return sum(1 for jid, j in self.jobs.items() if j["owner"] == owner
                   for i in self.items[jid] if i["status"] == "sent" and i["sent_at"] and i["sent_at"].date() == today)

    def active_job(self, owner):
        for j in self.jobs.values():
            if j["owner"] == owner and j["status"] in _ACTIVE:
                return dict(j)
        return None


class MariaStore:
    def __init__(self):
        from app.db import mariadb  # 지연 import — 테스트가 DB 없이 돈다
        self.db = mariadb

    def active_for_sheet(self, sid, sheet):
        return bool(self.db.fetch_one(
            "SELECT id FROM mail_send_jobs WHERE spreadsheet_id=%s AND sheet=%s AND status IN ('running','stopping')",
            (sid, sheet)))

    def insert_job(self, owner, sid, sheet, sender, rows):
        import json
        jid = self.db.execute_lastid(
            "INSERT INTO mail_send_jobs (owner, spreadsheet_id, sheet, sender, status, total) "
            "VALUES (%s,%s,%s,%s,'running',%s)", (owner, sid, sheet, sender, len(rows)))
        for r in rows:
            self.db.execute(
                "INSERT INTO mail_send_items (job_id, sheet_row, email, manager, row_values) VALUES (%s,%s,%s,%s,%s)",
                (jid, r.row, r.email, r.manager, json.dumps(r.values, ensure_ascii=False)))
        return jid

    def job(self, jid):
        return self.db.fetch_one("SELECT * FROM mail_send_jobs WHERE id=%s", (jid,))

    def pending_items(self, jid):
        import json
        rows = self.db.fetch_all(
            "SELECT * FROM mail_send_items WHERE job_id=%s AND status='pending' ORDER BY sheet_row", (jid,))
        for r in rows:
            r["row_values"] = json.loads(r["row_values"]) if isinstance(r["row_values"], str) else r["row_values"]
        return rows

    def set_item_status(self, jid, row, status, error=None, message_id=None):
        self.db.execute(
            "UPDATE mail_send_items SET status=%s, error=%s, message_id=%s, "
            "sent_at=IF(%s='sent', NOW(), sent_at) WHERE job_id=%s AND sheet_row=%s",
            (status, error, message_id, status, jid, row))

    def item_status(self, jid, row):
        r = self.db.fetch_one("SELECT status FROM mail_send_items WHERE job_id=%s AND sheet_row=%s", (jid, row))
        return r["status"] if r else None

    def bump(self, jid, col):
        assert col in ("sent", "failed", "skipped")
        self.db.execute(f"UPDATE mail_send_jobs SET {col}={col}+1 WHERE id=%s", (jid,))

    def set_job(self, jid, **kw):
        cols = ", ".join(f"{k}=%s" for k in kw)
        self.db.execute(f"UPDATE mail_send_jobs SET {cols} WHERE id=%s", (*kw.values(), jid))

    def interrupt_all(self):
        self.db.execute(
            "UPDATE mail_send_items i JOIN mail_send_jobs j ON i.job_id=j.id SET i.status='unknown' "
            "WHERE j.status IN ('running','stopping') AND i.status='sending'")
        self.db.execute("UPDATE mail_send_jobs SET status='interrupted', finished_at=NOW() "
                        "WHERE status IN ('running','stopping')")

    def sent_today(self, owner):
        r = self.db.fetch_one(
            "SELECT COUNT(*) AS n FROM mail_send_items i JOIN mail_send_jobs j ON i.job_id=j.id "
            "WHERE j.owner=%s AND i.status='sent' AND i.sent_at >= CURDATE()", (owner,))
        return int(r["n"]) if r else 0

    def active_job(self, owner):
        return self.db.fetch_one(
            "SELECT * FROM mail_send_jobs WHERE owner=%s AND status IN ('running','stopping') "
            "ORDER BY id DESC LIMIT 1", (owner,))


_store = None


def _s():
    global _store
    if _store is None:
        _store = MariaStore()
    return _store


def ensure_mail_sender_tables() -> None:
    from app.db.mariadb import execute
    for ddl in _DDL:
        execute(ddl)
    recover_after_restart()


def recover_after_restart() -> None:
    _s().interrupt_all()


def sent_today(email: str) -> int:
    return _s().sent_today(email)


def create_job(owner: str, spreadsheet_id: str, sheet: str, sender: str, rows: list[Row]) -> int:
    if _s().active_for_sheet(spreadsheet_id, sheet):
        raise RuntimeError("이 시트는 이미 발송 중입니다")
    return _s().insert_job(owner, spreadsheet_id, sheet, sender, rows)


def get_job(job_id: int, owner: str) -> dict | None:
    j = _s().job(job_id)
    return j if j and j["owner"] == owner else None


def active_job(owner: str) -> dict | None:
    return _s().active_job(owner)


def request_stop(job_id: int, owner: str) -> bool:
    j = get_job(job_id, owner)
    if not j or j["status"] != "running":
        return False
    _s().set_job(job_id, status="stopping")
    return True


@dataclass
class JobDeps:
    read_cell: Callable = mail_google.read_cell
    write_cells: Callable = mail_google.write_cells
    send_raw: Callable = mail_google.send_raw
    sleep: Callable = time.sleep


def _error_kind(e: Exception) -> str:
    return type(e).__name__ + ":" + str(e)[:80]


def run_job(job_id: int, creds, cols: Columns, templates: dict, deps: JobDeps | None = None) -> None:
    deps = deps or JobDeps()
    st = _s()
    job = st.job(job_id)
    last_kind, streak = None, 0
    for item in st.pending_items(job_id):
        if st.job(job_id)["status"] == "stopping":
            st.set_job(job_id, status="stopped", stop_reason="사용자가 중지함", finished_at=datetime.now())
            return
        row = item["sheet_row"]
        try:
            if deps.read_cell(creds, job["spreadsheet_id"], job["sheet"], row, cols.status) == "O":
                st.set_item_status(job_id, row, "skipped", error="다른 툴이 이미 보냄(O)")
                st.bump(job_id, "skipped")
                continue
            tpl = templates[item["manager"]]
            subject, html = render(tpl, item["row_values"])
            raw = build_mime(sender=job["sender"], to=item["email"], subject=subject, html=html, images=tpl.images)
            st.set_item_status(job_id, row, "sending")
            mid = deps.send_raw(creds, raw)
            st.set_item_status(job_id, row, "sent", message_id=mid)
            st.bump(job_id, "sent")
            last_kind, streak = None, 0
            updates = {cols.status: "O"}
            if cols.date is not None:
                updates[cols.date] = datetime.now().strftime("%Y-%m-%d")
            try:
                deps.write_cells(creds, job["spreadsheet_id"], job["sheet"], row, updates)
            except Exception as we:  # 발송은 됐다 — DB 에는 sent, 시트 기록 실패만 남긴다
                st.set_item_status(job_id, row, "sent", error=f"시트 기록 실패: {we}", message_id=mid)
        except Exception as e:
            st.set_item_status(job_id, row, "failed", error=str(e)[:500])
            st.bump(job_id, "failed")
            try:
                deps.write_cells(creds, job["spreadsheet_id"], job["sheet"], row,
                                 {cols.status: "X", cols.note: str(e)[:200]})
            except Exception:
                pass
            kind = _error_kind(e)
            streak = streak + 1 if kind == last_kind else 1
            last_kind = kind
            if streak >= MAX_SAME_ERROR:
                st.set_job(job_id, status="stopped", finished_at=datetime.now(),
                           stop_reason=f"같은 오류 {streak}회 연속으로 멈춤: {e}")
                return
        deps.sleep(SEND_DELAY)
    st.set_job(job_id, status="done", finished_at=datetime.now())


def start_job_thread(job_id: int, creds, cols: Columns, templates: dict) -> None:
    threading.Thread(target=run_job, args=(job_id, creds, cols, templates), daemon=True,
                     name=f"mail-send-{job_id}").start()
