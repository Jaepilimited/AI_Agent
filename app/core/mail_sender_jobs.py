# app/core/mail_sender_jobs.py
"""메일 발송 작업 — 기록은 MariaDB, 실행은 데몬 스레드.

⛔ 재시작 후 자동 재개하지 않는다: 'sending' 이던 행이 실제로 나갔는지 서버는 모른다.
⛔ 같은 원인 실패가 MAX_SAME_ERROR 번 연속이면 멈춘다 — 권한 문제로 전 행 X 가 되는 것을 막는다.
⛔ 같은 사람에게 두 번 보내지 않는 것이 실패보다 중요하다:
   - 보냈는지 모르는 행(전송 중 끊김·5xx)은 X(=다시 보내도 됨) 가 아니라 '불확실(unknown)' 로 남기고,
     사용자가 보낸편지함을 확인해 '확인했음' 을 누르기 전에는 같은 시트로 새 작업을 시작하지 않는다.
   - send_raw 가 돌아온 뒤의 기록 실패(DB·시트)는 발송 실패가 아니다 — 그 행은 보낸 것이다.
   - 토큰 만료·권한(401/403)·속도 제한(429)은 발송 결과가 아니다 — 작업만 멈추고 행은 대기로 되돌린다.
"""
from __future__ import annotations

import hashlib
import http.client
import json
import socket
import ssl
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

import httplib2
import requests
import structlog
from google.auth.exceptions import RefreshError
from googleapiclient.errors import HttpError

from app.core import mail_google
from app.core.mail_mime import build_mime
from app.core.mail_sender import DAILY_LIMIT, Columns, Row
from app.core.mail_template import render

logger = structlog.get_logger(__name__)

MAX_SAME_ERROR = 3
SEND_DELAY = 1.0
_ACTIVE = ("running", "stopping")
BUSY = "이 시트는 이미 발송 중입니다"
UNCERTAIN = "발송 여부 불확실 — 보낸편지함 확인"
EXPIRED_STOP = "구글 연결이 만료되었습니다 — 다시 연결 후 이어서 보내세요"
BUMP_COLS = frozenset({"sent", "failed", "skipped", "unknown", "sheet_write_failed"})
SET_JOB_KEYS = frozenset({"status", "stop_reason", "finished_at"})

_DDL = [
    """CREATE TABLE IF NOT EXISTS mail_send_jobs (
        id INT AUTO_INCREMENT PRIMARY KEY, owner VARCHAR(255) NOT NULL, spreadsheet_id VARCHAR(128) NOT NULL,
        sheet VARCHAR(255) NOT NULL, sender VARCHAR(255) NOT NULL, status VARCHAR(16) NOT NULL,
        total INT NOT NULL DEFAULT 0, sent INT NOT NULL DEFAULT 0, failed INT NOT NULL DEFAULT 0,
        skipped INT NOT NULL DEFAULT 0, sheet_write_failed INT NOT NULL DEFAULT 0,
        `unknown` INT NOT NULL DEFAULT 0, stop_reason TEXT NULL,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, finished_at DATETIME NULL,
        INDEX idx_owner (owner, created_at), INDEX idx_sheet (spreadsheet_id, sheet, status)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS mail_send_items (
        id INT AUTO_INCREMENT PRIMARY KEY, job_id INT NOT NULL, sheet_row INT NOT NULL,
        email VARCHAR(320) NOT NULL, manager VARCHAR(255) NOT NULL, row_values JSON NOT NULL,
        status VARCHAR(16) NOT NULL DEFAULT 'pending', error TEXT NULL, message_id VARCHAR(64) NULL,
        sent_at DATETIME NULL, UNIQUE KEY uq_job_row (job_id, sheet_row), INDEX idx_sent (status, sent_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    # 먼저 만들어진 테이블에 새 카운터 열 (MariaDB 는 IF NOT EXISTS 를 지원한다)
    "ALTER TABLE mail_send_jobs ADD COLUMN IF NOT EXISTS sheet_write_failed INT NOT NULL DEFAULT 0",
    "ALTER TABLE mail_send_jobs ADD COLUMN IF NOT EXISTS `unknown` INT NOT NULL DEFAULT 0",
]


def _check_bump(col: str) -> None:
    assert col in BUMP_COLS, f"허용되지 않은 카운터: {col}"


def _check_set_job(kw: dict) -> None:
    assert kw and set(kw) <= SET_JOB_KEYS, f"허용되지 않은 작업 열: {sorted(set(kw) - SET_JOB_KEYS)}"


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
        with self._lock:  # 진행 중 확인과 삽입을 한 덩어리로 — 동시 요청 둘 중 하나만 통과
            if self.active_for_sheet(sid, sheet):
                raise RuntimeError(BUSY)
            jid = len(self.jobs) + 1
            self.jobs[jid] = dict(id=jid, owner=owner, spreadsheet_id=sid, sheet=sheet, sender=sender,
                                  status="running", total=len(rows), sent=0, failed=0, skipped=0,
                                  sheet_write_failed=0, unknown=0, stop_reason=None,
                                  created_at=datetime.now(), finished_at=None)
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

    def bump(self, jid, col, n=1):
        _check_bump(col)
        self.jobs[jid][col] += n

    def set_job(self, jid, **kw):
        _check_set_job(kw)
        self.jobs[jid].update(kw)

    def resolve_sending(self, jid):
        n = 0
        for i in self.items.get(jid, []):
            if i["status"] == "sending":
                i.update(status="unknown", error=UNCERTAIN)
                n += 1
        return n

    def interrupt_all(self):
        for jid, j in self.jobs.items():
            if j["status"] in _ACTIVE:
                j["unknown"] += self.resolve_sending(jid)
                j["status"] = "interrupted"
                j["finished_at"] = datetime.now()

    def unknown_rows(self, jid):
        return sorted(i["sheet_row"] for i in self.items.get(jid, []) if i["status"] == "unknown")

    def unresolved_unknown(self, sid, sheet):
        return [{"job_id": jid, "sheet_row": i["sheet_row"], "owner": j["owner"]}
                for jid, j in sorted(self.jobs.items()) if j["spreadsheet_id"] == sid and j["sheet"] == sheet
                for i in sorted(self.items[jid], key=lambda x: x["sheet_row"]) if i["status"] == "unknown"]

    def ack_unknown(self, jid):
        n = 0
        for i in self.items.get(jid, []):
            if i["status"] == "unknown":
                i["status"] = "acknowledged"
                n += 1
        return n

    def sent_today(self, owner):
        today = datetime.now().date()
        return sum(1 for jid, j in self.jobs.items() if j["owner"] == owner
                   for i in self.items[jid] if i["status"] == "sent" and i["sent_at"] and i["sent_at"].date() == today)

    def active_job(self, owner):
        for j in self.jobs.values():
            if j["owner"] == owner and j["status"] in _ACTIVE:
                return dict(j)
        return None


def _sheet_lock_name(sid: str, sheet: str) -> str:
    # MariaDB 잠금 이름은 64자 제한 — 시트를 해시로 줄인다
    return "mail_sheet:" + hashlib.sha1(f"{sid}|{sheet}".encode("utf-8")).hexdigest()[:40]


class MariaStore:
    def __init__(self):
        from app.db import mariadb  # 지연 import — 테스트가 DB 없이 돈다
        self.db = mariadb

    def active_for_sheet(self, sid, sheet):
        return bool(self.db.fetch_one(
            "SELECT id FROM mail_send_jobs WHERE spreadsheet_id=%s AND sheet=%s AND status IN ('running','stopping')",
            (sid, sheet)))

    def insert_job(self, owner, sid, sheet, sender, rows):
        """진행 중 확인 + 작업·항목 삽입을 한 연결·한 트랜잭션에서, 시트 이름 잠금(GET_LOCK) 아래 한다.

        ⛔ 확인과 삽입 사이에 다른 요청이 끼어들면 같은 시트에 작업이 둘 생겨 같은 사람에게 두 번 간다.
        """
        lock = _sheet_lock_name(sid, sheet)
        conn = self.db.get_maria_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT GET_LOCK(%s, 10) AS got", (lock,))
                got = (cur.fetchone() or {}).get("got")
                if got != 1:
                    raise RuntimeError("이 시트의 발송 시작을 확인하지 못했습니다 — 잠시 뒤 다시 시도하세요")
                try:
                    conn.commit()  # 잠금 전에 잡힌 읽기 시점을 버리고 최신 커밋을 본다
                    cur.execute("SELECT id FROM mail_send_jobs WHERE spreadsheet_id=%s AND sheet=%s "
                                "AND status IN ('running','stopping') LIMIT 1", (sid, sheet))
                    if cur.fetchone():
                        raise RuntimeError(BUSY)
                    cur.execute("INSERT INTO mail_send_jobs (owner, spreadsheet_id, sheet, sender, status, total) "
                                "VALUES (%s,%s,%s,%s,'running',%s)", (owner, sid, sheet, sender, len(rows)))
                    jid = cur.lastrowid
                    cur.executemany(
                        "INSERT INTO mail_send_items (job_id, sheet_row, email, manager, row_values) "
                        "VALUES (%s,%s,%s,%s,%s)",
                        [(jid, r.row, r.email, r.manager, json.dumps(r.values, ensure_ascii=False)) for r in rows])
                    conn.commit()
                    return jid
                except Exception:
                    conn.rollback()
                    raise
                finally:
                    try:
                        cur.execute("SELECT RELEASE_LOCK(%s)", (lock,))
                    except Exception as e:  # 연결이 죽었으면 잠금도 세션과 함께 풀린다
                        logger.warning("mail_sheet_lock_release_failed", error=str(e))
        finally:
            conn.close()

    def job(self, jid):
        return self.db.fetch_one("SELECT * FROM mail_send_jobs WHERE id=%s", (jid,))

    def pending_items(self, jid):
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

    def bump(self, jid, col, n=1):
        _check_bump(col)
        self.db.execute(f"UPDATE mail_send_jobs SET `{col}`=`{col}`+%s WHERE id=%s", (n, jid))

    def set_job(self, jid, **kw):
        _check_set_job(kw)
        cols = ", ".join(f"`{k}`=%s" for k in kw)
        self.db.execute(f"UPDATE mail_send_jobs SET {cols} WHERE id=%s", (*kw.values(), jid))

    def resolve_sending(self, jid):
        return self.db.execute(
            "UPDATE mail_send_items SET status='unknown', error=%s WHERE job_id=%s AND status='sending'",
            (UNCERTAIN, jid))

    def interrupt_all(self):
        self.db.execute(
            "UPDATE mail_send_jobs j SET j.`unknown` = j.`unknown` + (SELECT COUNT(*) FROM mail_send_items i "
            "WHERE i.job_id=j.id AND i.status='sending') WHERE j.status IN ('running','stopping')")
        self.db.execute(
            "UPDATE mail_send_items i JOIN mail_send_jobs j ON i.job_id=j.id SET i.status='unknown', i.error=%s "
            "WHERE j.status IN ('running','stopping') AND i.status='sending'", (UNCERTAIN,))
        self.db.execute("UPDATE mail_send_jobs SET status='interrupted', finished_at=NOW() "
                        "WHERE status IN ('running','stopping')")

    def unknown_rows(self, jid):
        return [r["sheet_row"] for r in self.db.fetch_all(
            "SELECT sheet_row FROM mail_send_items WHERE job_id=%s AND status='unknown' ORDER BY sheet_row", (jid,))]

    def unresolved_unknown(self, sid, sheet):
        return [{"job_id": r["job_id"], "sheet_row": r["sheet_row"], "owner": r["owner"]} for r in self.db.fetch_all(
            "SELECT i.job_id, i.sheet_row, j.owner FROM mail_send_items i JOIN mail_send_jobs j ON i.job_id=j.id "
            "WHERE j.spreadsheet_id=%s AND j.sheet=%s AND i.status='unknown' ORDER BY i.job_id, i.sheet_row",
            (sid, sheet))]

    def ack_unknown(self, jid):
        return self.db.execute(
            "UPDATE mail_send_items SET status='acknowledged' WHERE job_id=%s AND status='unknown'", (jid,))

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
    from app.db import mariadb
    for ddl in _DDL:
        mariadb.execute(ddl)
    recover_after_restart()


def recover_after_restart() -> None:
    _s().interrupt_all()


def sent_today(email: str) -> int:
    return _s().sent_today(email)


def create_job(owner: str, spreadsheet_id: str, sheet: str, sender: str, rows: list[Row]) -> int:
    """같은 시트가 진행 중이면 RuntimeError(BUSY) — 확인과 삽입은 저장소 안에서 원자적으로."""
    return _s().insert_job(owner, spreadsheet_id, sheet, sender, rows)


def get_job(job_id: int, owner: str) -> dict | None:
    st = _s()
    j = st.job(job_id)
    if not j or j["owner"] != owner:
        return None
    j = dict(j)
    j["unknown_rows"] = st.unknown_rows(job_id)
    return j


def active_job(owner: str) -> dict | None:
    return _s().active_job(owner)


def request_stop(job_id: int, owner: str) -> bool:
    j = get_job(job_id, owner)
    if not j or j["status"] != "running":
        return False
    _s().set_job(job_id, status="stopping")
    return True


def unresolved_unknown(spreadsheet_id: str, sheet: str) -> list[dict]:
    """이 시트에서 '보냈는지 모름' 으로 남아 아직 확인(ack)받지 못한 행들."""
    return _s().unresolved_unknown(spreadsheet_id, sheet)


def ack_unknown(job_id: int, owner: str) -> int | None:
    """작업 주인이 보낸편지함을 확인했다고 표시 — 남의 작업이면 None."""
    if get_job(job_id, owner) is None:
        return None
    return _s().ack_unknown(job_id)


@dataclass
class JobDeps:
    read_cell: Callable = mail_google.read_cell
    write_cells: Callable = mail_google.write_cells
    send_raw: Callable = mail_google.send_raw
    sleep: Callable = time.sleep


# 요청이 구글에 닿았는지 알 수 없는 전송 계층 오류 — 보냈을 수도 있다
_TRANSPORT_UNCERTAIN = (TimeoutError, socket.timeout, ConnectionError, ssl.SSLError, http.client.HTTPException,
                        requests.exceptions.ConnectionError, requests.exceptions.Timeout, httplib2.HttpLib2Error)


def _http_status(e: Exception) -> int:
    try:
        return int(getattr(getattr(e, "resp", None), "status", 0) or 0)
    except (TypeError, ValueError):
        return 0


def _uncertain(e: Exception) -> bool:
    if isinstance(e, HttpError):
        return _http_status(e) >= 500
    return isinstance(e, _TRANSPORT_UNCERTAIN)


def _account_problem(e: Exception) -> str | None:
    """발송 결과가 아니라 계정·연결 문제 — 작업을 멈추고 행은 대기로 둔다 (X 금지)."""
    if isinstance(e, RefreshError):
        return EXPIRED_STOP
    if isinstance(e, HttpError):
        code = _http_status(e)
        if code in (401, 403):
            return f"구글 권한 오류({code})로 멈춤 — 연결·권한을 확인한 뒤 이어서 보내세요: {getattr(e, 'reason', '') or e}"
        if code == 429:
            return "구글 발송 속도·한도 제한(429)으로 멈춤 — 잠시 뒤 이어서 보내세요"
    return None


def _error_kind(e: Exception) -> str:
    """연속 오류 판정 키 — 메시지(요청 ID 등이 섞인다)가 아니라 예외 종류와 HTTP 상태로."""
    kind = type(e).__name__
    if isinstance(e, HttpError):
        kind += f":{_http_status(e)}"
    return kind


def _compose(job: dict, item: dict, templates: dict) -> bytes:
    manager = item["manager"]
    tpl = templates.get(manager)
    if tpl is None:
        raise RuntimeError(f"템플릿 없음: {manager}")
    if isinstance(tpl, Exception):
        raise RuntimeError(f"템플릿 오류: {manager} ({tpl})")
    subject, html = render(tpl, item["row_values"])
    return build_mime(sender=job["sender"], to=item["email"], subject=subject, html=html, images=tpl.images)


def _finish(st, job_id: int, status: str, reason: str | None) -> None:
    # 'sending' 으로 남은 행(발송 뒤 DB 기록 실패 등)은 보냈는지 모르는 행이다 — 불확실로 돌린다
    try:
        n = st.resolve_sending(job_id)
        if n:
            st.bump(job_id, "unknown", n)
    except Exception as e:
        logger.error("mail_send_resolve_sending_failed", job_id=job_id, error=str(e))
    st.set_job(job_id, status=status, stop_reason=reason, finished_at=datetime.now())


def _write_sheet(deps, creds, job, row, updates, job_id) -> None:
    try:
        deps.write_cells(creds, job["spreadsheet_id"], job["sheet"], row, updates)
    except Exception as e:  # 실패 표시를 못 남겨도 발송 판단은 이미 끝났다
        logger.warning("mail_send_sheet_note_failed", job_id=job_id, row=row, error=str(e))


def _mark_failed(st, deps, creds, job, cols, job_id, row, e) -> None:
    msg = (str(e) or type(e).__name__)[:500]
    st.set_item_status(job_id, row, "failed", error=msg)
    st.bump(job_id, "failed")
    _write_sheet(deps, creds, job, row, {cols.status: "X", cols.note: msg[:200]}, job_id)


def _mark_unknown(st, deps, creds, job, cols, job_id, row, e) -> None:
    """보냈을 수도 있다 — X 를 쓰지 않는다 (X 는 '다시 보내도 됨' 으로 읽힌다)."""
    logger.warning("mail_send_uncertain", job_id=job_id, row=row, error=f"{type(e).__name__}: {e}")
    st.set_item_status(job_id, row, "unknown", error=f"{UNCERTAIN} ({type(e).__name__}: {e})"[:500])
    st.bump(job_id, "unknown")
    _write_sheet(deps, creds, job, row, {cols.note: UNCERTAIN}, job_id)


def _record_sent(st, deps, creds, job, cols, job_id, row, mid) -> Exception | None:
    """발송 뒤 기록 — 여기서의 실패는 무엇이든 '발송 실패' 가 아니다. 시트 기록 실패면 그 예외를 돌려준다."""
    try:
        st.set_item_status(job_id, row, "sent", message_id=mid)
    except Exception as e:
        logger.error("mail_send_record_failed", job_id=job_id, row=row, message_id=mid, step="item", error=str(e))
    try:
        st.bump(job_id, "sent")
    except Exception as e:
        logger.error("mail_send_record_failed", job_id=job_id, row=row, message_id=mid, step="counter", error=str(e))
    updates = {cols.status: "O"}
    if cols.date is not None:
        updates[cols.date] = datetime.now().strftime("%Y-%m-%d")
    try:
        deps.write_cells(creds, job["spreadsheet_id"], job["sheet"], row, updates)
        return None
    except Exception as we:
        logger.warning("mail_send_sheet_write_failed", job_id=job_id, row=row, message_id=mid, error=str(we))
        try:
            st.set_item_status(job_id, row, "sent", error=f"시트 기록 실패: {we}"[:500], message_id=mid)
            st.bump(job_id, "sheet_write_failed")
        except Exception as e:
            logger.error("mail_send_record_failed", job_id=job_id, row=row, message_id=mid, step="sheet_note",
                         error=str(e))
        return we


def run_job(job_id: int, creds, cols: Columns, templates: dict, deps: JobDeps | None = None) -> None:
    deps = deps or JobDeps()
    st = _s()
    job = st.job(job_id)
    sid, sheet, owner = job["spreadsheet_id"], job["sheet"], job["owner"]
    last_kind, streak, sheet_streak = None, 0, 0
    for item in st.pending_items(job_id):
        if st.job(job_id)["status"] == "stopping":
            return _finish(st, job_id, "stopped", "사용자가 중지함")
        if st.sent_today(owner) >= DAILY_LIMIT:
            return _finish(st, job_id, "stopped", f"오늘 발송 한도({DAILY_LIMIT}통)에 도달해 멈춤 — 내일 이어서 보내세요")
        row = item["sheet_row"]
        failure: Exception | None = None
        unsure = False
        # ① 발송 직전 G열 재확인 + 조립 — 여기서 실패하면 아직 아무것도 나가지 않았다
        try:
            current = deps.read_cell(creds, sid, sheet, row, cols.status)
            already = str(current or "").strip().upper() == "O"
            raw = None if already else _compose(job, item, templates)
        except Exception as e:
            failure = e
        if failure is None and already:
            st.set_item_status(job_id, row, "skipped", error="다른 툴이 이미 보냄(O)")
            st.bump(job_id, "skipped")
            continue
        # ② 발송
        if failure is None:
            st.set_item_status(job_id, row, "sending")
            try:
                mid = deps.send_raw(creds, raw)
            except Exception as e:
                failure, unsure = e, _uncertain(e)
        if failure is not None:
            halt = _account_problem(failure)
            if halt:  # 안 나갔다 — 행을 대기로 되돌리고 작업만 멈춘다
                st.set_item_status(job_id, row, "pending")
                return _finish(st, job_id, "stopped", halt)
            if unsure:
                _mark_unknown(st, deps, creds, job, cols, job_id, row, failure)
            else:
                _mark_failed(st, deps, creds, job, cols, job_id, row, failure)
            kind = _error_kind(failure)
            streak = streak + 1 if kind == last_kind else 1
            last_kind = kind
            if streak >= MAX_SAME_ERROR:
                return _finish(st, job_id, "stopped", f"같은 오류 {streak}회 연속으로 멈춤: {failure}"[:1000])
            deps.sleep(SEND_DELAY)
            continue
        # ③ 나갔다 — 이후 무엇이 실패해도 이 행은 '보냄' 이다
        last_kind, streak = None, 0
        sheet_err = _record_sent(st, deps, creds, job, cols, job_id, row, mid)
        if sheet_err is None:
            sheet_streak = 0
        else:
            sheet_streak += 1
            halt = _account_problem(sheet_err)
            if halt or sheet_streak >= MAX_SAME_ERROR:
                # 시트에 O 가 안 남으면 다른 툴이 다시 보낼 수 있다 — 계속 보내지 않는다
                return _finish(st, job_id, "stopped", halt or (
                    f"시트에 O 기록이 {sheet_streak}회 연속 실패해 멈춤 — 보낸 행은 발송 기록에 남아 있습니다: {sheet_err}"
                )[:1000])
        deps.sleep(SEND_DELAY)
    _finish(st, job_id, "done", None)


def _thread_main(job_id: int, creds, cols: Columns, templates: dict, deps: JobDeps | None = None) -> None:
    """스레드 대상 — 예상 못 한 예외로 작업이 'running' 에 영원히 남지 않게 한다."""
    try:
        run_job(job_id, creds, cols, templates, deps)
    except Exception as e:
        logger.exception("mail_send_job_crashed", job_id=job_id)
        try:
            _finish(_s(), job_id, "stopped", f"내부 오류: {e}"[:1000])
        except Exception:
            logger.exception("mail_send_job_crash_unrecorded", job_id=job_id)


def start_job_thread(job_id: int, creds, cols: Columns, templates: dict) -> None:
    threading.Thread(target=_thread_main, args=(job_id, creds, cols, templates), daemon=True,
                     name=f"mail-send-{job_id}").start()
