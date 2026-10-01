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
READ_FAILED = "시트 확인 실패 — 다시 시도"
HISTORY = "발송 기록 있음"
# 이 상태의 발송 기록이 있는 행은 누구의 작업이든 다시 대상에 넣지 않는다 (확인된 불확실 행은 제외)
_BLOCKING = ("sent", "sending", "unknown")
_BLOCK_RANK = {"sent": 0, "sending": 1, "unknown": 2}


class TargetsChanged(RuntimeError):
    """잠금 안에서 다시 따진 대상 수가 확인 창의 숫자와 다르다."""
    def __init__(self, expected: int, actual: int):
        super().__init__(f"대상이 바뀌었습니다 ({expected} → {actual}명). 다시 점검하세요")


def norm_email(email: str | None) -> str:
    return (email or "").strip().lower()


def blocking_record(history: list[dict], row: int, email: str) -> dict | None:
    """이 행·수신자를 막는 발송 기록 — 없으면 None. 결과: {status, by: 행|이메일, owner, job_id}.

    - 보냄(sent): **이메일로만** 막는다. 정렬로 행이 옮겨가도 같은 사람은 막고,
      옛 행 번호를 새로 쓴 다른 사람은 막지 않는다.
    - 발송 중·불확실(sending/unknown): 행 **또는** 이메일 — 확실하지 않으면 엄격하게.
    - 여럿이면 보냄 > 발송 중 > 불확실 순으로 대표를 고른다.
    """
    em = norm_email(email)
    best = None
    for h in history:
        if h["status"] not in _BLOCKING:
            continue
        same_email = bool(em) and h["email"] == em
        if h["status"] == "sent":
            by = "이메일" if same_email else None
        else:
            by = "행" if h["sheet_row"] == row else ("이메일" if same_email else None)
        if by and (best is None or _BLOCK_RANK[h["status"]] < _BLOCK_RANK[best["status"]]):
            best = {"status": h["status"], "by": by, "owner": h["owner"], "job_id": h["job_id"]}
    return best


def own_unknown_jobs(history: list[dict], owner: str) -> list[dict]:
    """호출자 본인 작업 중 '확인했음' 이 필요한 것 — 불확실 행과, 끝난 작업에 남은 '발송 중' 행."""
    by_job: dict[int, set] = {}
    for h in history:
        orphan = h["status"] == "sending" and h["job_status"] not in _ACTIVE
        if h["owner"] == owner and (h["status"] == "unknown" or orphan):
            by_job.setdefault(h["job_id"], set()).add(h["sheet_row"])
    return [{"job_id": jid, "rows": sorted(rows)} for jid, rows in sorted(by_job.items())]


def _keep_targets(rows, history, confirm_count):
    kept = [r for r in rows if blocking_record(history, r.row, r.email) is None]
    if confirm_count is not None and len(kept) != confirm_count:
        raise TargetsChanged(confirm_count, len(kept))
    return kept
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

    def insert_job(self, owner, sid, sheet, sender, rows, confirm_count=None):
        with self._lock:  # 진행 중 확인·발송 기록 확인·삽입을 한 덩어리로 — 동시 요청 둘 중 하나만 통과
            if self.active_for_sheet(sid, sheet):
                raise RuntimeError(BUSY)
            rows = _keep_targets(rows, self.history(sid, sheet), confirm_count)
            jid = max(self.jobs, default=0) + 1
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

    def history(self, sid, sheet, exclude_job=None, row=None, email=None):
        em = norm_email(email)
        out = []
        for jid, j in sorted(self.jobs.items()):
            if jid == exclude_job or j["spreadsheet_id"] != sid or j["sheet"] != sheet:
                continue
            for i in self.items[jid]:
                if i["status"] not in _BLOCKING:
                    continue
                if row is not None and not (i["sheet_row"] == row or norm_email(i["email"]) == em):
                    continue
                out.append(dict(job_id=jid, owner=j["owner"], sheet_row=i["sheet_row"],
                                email=norm_email(i["email"]), status=i["status"], job_status=j["status"]))
        return out

    def ack_unknown(self, jid):
        n = 0
        ended = self.jobs[jid]["status"] not in _ACTIVE
        for i in self.items.get(jid, []):
            if i["status"] == "unknown" or (i["status"] == "sending" and ended):
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

    _HISTORY_SQL = ("SELECT i.job_id, j.owner, i.sheet_row, LOWER(TRIM(i.email)) AS email, i.status, "
                    "j.status AS job_status FROM mail_send_items i JOIN mail_send_jobs j ON i.job_id=j.id "
                    "WHERE j.spreadsheet_id=%s AND j.sheet=%s AND i.status IN ('sent','sending','unknown')")

    def insert_job(self, owner, sid, sheet, sender, rows, confirm_count=None):
        """진행 중 확인 + 발송 기록 확인 + 작업·항목 삽입을 한 연결·한 트랜잭션에서, 시트 이름 잠금(GET_LOCK) 아래 한다.

        ⛔ 확인과 삽입 사이에 다른 요청이 끼어들면 같은 시트에 작업이 둘 생겨 같은 사람에게 두 번 간다.
        ⛔ 점검 뒤 새로 생긴 발송 기록(보냄·불확실) 행은 여기서 다시 빼고, 숫자가 달라지면 시작하지 않는다.
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
                    cur.execute(self._HISTORY_SQL, (sid, sheet))
                    rows = _keep_targets(rows, list(cur.fetchall()), confirm_count)
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

    def history(self, sid, sheet, exclude_job=None, row=None, email=None):
        sql, params = self._HISTORY_SQL, [sid, sheet]
        if exclude_job is not None:
            sql += " AND i.job_id<>%s"
            params.append(exclude_job)
        if row is not None:  # 이 행 또는 이 수신자의 기록만
            sql += " AND (i.sheet_row=%s OR LOWER(TRIM(i.email))=%s)"
            params += [row, norm_email(email)]
        return [dict(r) for r in self.db.fetch_all(sql, tuple(params))]

    def ack_unknown(self, jid):
        # 불확실 행 + 끝난 작업에 남은 '발송 중' 행 (재시작·충돌로 정리되지 못한 것)
        return self.db.execute(
            "UPDATE mail_send_items i JOIN mail_send_jobs j ON i.job_id=j.id SET i.status='acknowledged' "
            "WHERE i.job_id=%s AND (i.status='unknown' OR "
            "(i.status='sending' AND j.status NOT IN ('running','stopping')))", (jid,))

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


def create_job(owner: str, spreadsheet_id: str, sheet: str, sender: str, rows: list[Row],
               confirm_count: int | None = None) -> int:
    """같은 시트가 진행 중이면 RuntimeError(BUSY). 발송 기록 있는 행은 잠금 안에서 다시 빼고,
    남은 수가 confirm_count 와 다르면 TargetsChanged — 확인과 삽입은 저장소 안에서 원자적으로."""
    return _s().insert_job(owner, spreadsheet_id, sheet, sender, rows, confirm_count=confirm_count)


def sheet_history(spreadsheet_id: str, sheet: str) -> list[dict]:
    """이 시트의 막는 발송 기록(보냄·발송 중·확인 안 된 불확실) — 누구의 작업이든.
    각 항목: job_id, owner, sheet_row, email(정규화), status, job_status. 판정은 blocking_record."""
    return _s().history(spreadsheet_id, sheet)


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


def _mark_read_failed(st, deps, creds, job, cols, job_id, row, e) -> None:
    """G열을 못 읽었다 — 이미 O 일 수도 있으니 G 는 절대 쓰지 않는다 (H 메모만)."""
    st.set_item_status(job_id, row, "failed", error=f"{READ_FAILED} ({type(e).__name__}: {e})"[:500])
    st.bump(job_id, "failed")
    _write_sheet(deps, creds, job, row, {cols.note: READ_FAILED}, job_id)


def run_job(job_id: int, creds, cols: Columns, templates: dict, deps: JobDeps | None = None) -> None:
    deps = deps or JobDeps()
    st = _s()
    job = st.job(job_id)
    sid, sheet, owner = job["spreadsheet_id"], job["sheet"], job["owner"]
    last_kind, streak, sheet_streak = None, 0, 0

    def same_error_streak(e: Exception) -> bool:
        nonlocal last_kind, streak
        kind = _error_kind(e)
        streak = streak + 1 if kind == last_kind else 1
        last_kind = kind
        return streak >= MAX_SAME_ERROR

    def streak_reason(e: Exception) -> str:
        return f"같은 오류 {streak}회 연속으로 멈춤: {e}"[:1000]

    for item in st.pending_items(job_id):
        status = st.job(job_id)["status"]
        if status != "running":
            if status == "stopping":
                return _finish(st, job_id, "stopped", "사용자가 중지함")
            return  # 다른 곳에서 이미 끝냈다(중단됨 등) — 보내지도, 상태를 덮어쓰지도 않는다
        if st.sent_today(owner) >= DAILY_LIMIT:
            return _finish(st, job_id, "stopped", f"오늘 발송 한도({DAILY_LIMIT}통)에 도달해 멈춤 — 내일 이어서 보내세요")
        row = item["sheet_row"]
        # ① 발송 직전 G열 재확인 — 읽기에 실패하면 G 는 건드리지 않는다
        try:
            current = deps.read_cell(creds, sid, sheet, row, cols.status)
        except Exception as e:
            halt = _account_problem(e)
            if halt:
                return _finish(st, job_id, "stopped", halt)
            _mark_read_failed(st, deps, creds, job, cols, job_id, row, e)
            if same_error_streak(e):
                return _finish(st, job_id, "stopped", streak_reason(e))
            deps.sleep(SEND_DELAY)
            continue
        if str(current or "").strip().upper() == "O":
            st.set_item_status(job_id, row, "skipped", error="다른 툴이 이미 보냄(O)")
            st.bump(job_id, "skipped")
            continue
        # ② 다른 작업의 발송 기록(보냄=이메일, 발송 중·불확실=행 또는 이메일) — 시트에 O 가 없어도 다시 보내지 않는다
        block = blocking_record(st.history(sid, sheet, exclude_job=job_id, row=row, email=item["email"]),
                                row, item["email"])
        if block:
            st.set_item_status(job_id, row, "skipped", error=f"{HISTORY} ({block['status']}, {block['by']})")
            st.bump(job_id, "skipped")
            continue
        # ③ 조립 + 발송
        failure: Exception | None = None
        unsure = False
        try:
            raw = _compose(job, item, templates)
        except Exception as e:
            failure = e
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
            if same_error_streak(failure):
                return _finish(st, job_id, "stopped", streak_reason(failure))
            deps.sleep(SEND_DELAY)
            continue
        # ④ 나갔다 — 이후 무엇이 실패해도 이 행은 '보냄' 이다
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
    if st.job(job_id)["status"] in _ACTIVE:  # 그사이 다른 곳에서 끝낸 작업은 덮어쓰지 않는다
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
