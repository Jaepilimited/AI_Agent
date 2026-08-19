# -*- coding: utf-8 -*-
"""보고서 저장·열람 권한.

**만든 사람과, 만든 사람이 지목한 사람만 연다** (admin 도 예외가 아니다 — 2026-08-12 결정).
보고서에는 원가·마진·거래처별 FOC율이 들어가므로 매출 질문과 같은 수준으로 열지 않는다.

공유는 **사람을 지목하는 방식**이다 (2026-08-13 추가). 링크를 아는 사람이 모두 열 수 있는
방식은 만들지 않았다 — 잔디·메일로 링크가 한 번 새면 회수할 수단이 없고, 이 문서에 든 것이
원가와 거래처별 마진이기 때문이다. 대신 **URL 은 그대로 붙여도 된다**: 지목된 사람만 열리고
나머지는 404 를 본다. 누구에게 공유했는지는 언제든 보고 되돌릴 수 있다.

권한 판정 원칙은 FI 와 같다:
    - **반드시 서버에서 DB 조회로** 판정한다. JWT·프론트 값은 stale 위험이 있어 믿지 않는다
    - 판정은 `get_for_user()` **한 곳**이다. 소유자냐 공유받았느냐를 여기서 한 번에 본다 —
      호출부에 조건을 흩으면 한 곳만 고치고 뚫린다
    - 공유를 **거는 쪽**(`share_add`)도 소유자 확인을 SQL 안에서 한다. 파이썬에서 먼저
      확인하고 나중에 INSERT 하면 그 사이가 비고, 확인을 빠뜨린 호출부가 생긴다

HTML 본문은 파일로 두고 DB 에는 경로만 둔다 (수십 KB 를 DB 에 넣지 않기 위해).
파일이 사라져도 payload 로 다시 렌더할 수 있게 payload 는 DB 에 넣는다.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

import structlog

from app.db.mariadb import execute, execute_lastid, fetch_all, fetch_one

logger = structlog.get_logger(__name__)

REPORT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data", "reports",
)

_DDL = """
CREATE TABLE IF NOT EXISTS reports (
    id            INT AUTO_INCREMENT PRIMARY KEY,
    user_id       INT NOT NULL,
    spec          VARCHAR(64) NOT NULL,
    title         VARCHAR(255) NOT NULL,
    params_hash   CHAR(32) NOT NULL,
    params_json   TEXT NOT NULL,
    payload_json  MEDIUMTEXT NULL,
    html_path     VARCHAR(512) NULL,
    question      VARCHAR(1000) NULL,
    gates_failed  INT NOT NULL DEFAULT 0,
    elapsed_sec   FLOAT NULL,
    created_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_user_created (user_id, created_at),
    INDEX idx_spec_hash (spec, params_hash, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""


_SHARE_DDL = """
CREATE TABLE IF NOT EXISTS report_shares (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    report_id   INT NOT NULL,
    user_id     INT NOT NULL,
    shared_by   INT NOT NULL,
    created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    seen_at     DATETIME NULL,
    UNIQUE KEY uk_report_user (report_id, user_id),
    INDEX idx_user (user_id, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""


def ensure_seen_column() -> None:
    """이미 만들어진 report_shares 에 seen_at 을 붙인다 (idempotent).

    알림은 **별도 테이블을 두지 않는다** — 공유 사실은 이미 이 표에 있다.
    사본을 만들면 공유 해제·삭제와 동기화가 어긋난다.
    """
    try:
        rows = fetch_all("SHOW COLUMNS FROM report_shares LIKE 'seen_at'")
        if not rows:
            execute("ALTER TABLE report_shares ADD COLUMN seen_at DATETIME NULL")
            logger.info("report_shares_seen_at_added")
    except Exception as e:
        logger.debug("report_shares_alter_skip", error=str(e)[:120])


def ensure_report_tables() -> None:
    try:
        execute(_DDL)
        execute(_SHARE_DDL)
        ensure_seen_column()
        os.makedirs(REPORT_DIR, exist_ok=True)
    except Exception as e:  # 기동을 막지 않는다
        logger.debug("report_ddl_skip", error=str(e)[:120])


def _code_version() -> str:
    """payload 를 만드는 코드의 지문.

    ⚠️ 이게 없으면 **코드를 고쳐 배포해도 캐시가 옛 payload 를 계속 돌려준다.**
    실제로 겪었다 (2026-08-12): 누적 열·라벨을 고쳐 배포했는데 보고서가 그대로여서
    한참 헤맸다. 표시(템플릿)는 렌더 때마다 새로 그려지니 일부만 바뀌어 더 헷갈렸다.
    `sql_cache` 가 고친 뒤에도 옛 SQL 을 재생하던 것과 같은 함정이다.
    """
    global _CODE_VER
    if _CODE_VER:
        return _CODE_VER
    h = hashlib.md5()
    here = os.path.dirname(os.path.abspath(__file__))
    for name in ("blocks.py", "semantic.py", "dynamic.py", "planner.py"):
        try:
            with open(os.path.join(here, name), "rb") as fh:
                h.update(fh.read())
        except OSError:
            pass
    _CODE_VER = h.hexdigest()[:8]
    return _CODE_VER


_CODE_VER = ""


def params_hash(spec_id: str, params: Dict[str, Any]) -> str:
    """캐시 키. 값이 같고 **만드는 코드도 같으면** 같은 보고서다."""
    blob = json.dumps({"spec": spec_id, "params": params, "code": _code_version()},
                      sort_keys=True, default=str)
    return hashlib.md5(blob.encode("utf-8")).hexdigest()


def find_fresh(spec_id: str, phash: str, max_age_hours: int = 12) -> Optional[Dict[str, Any]]:
    """같은 스펙·파라미터의 최근 보고서. 열 명이 같은 걸 물어도 BigQuery 는 한 번만 훑는다.

    ⚠️ **소유자를 보지 않는다** — 여기서 나온 payload 로 요청자 본인의 새 행을 만든다.
    남의 보고서 행을 그대로 돌려주면 열람 권한이 무너진다.
    """
    return fetch_one(
        "SELECT id, payload_json, title FROM reports "
        "WHERE spec = %s AND params_hash = %s AND payload_json IS NOT NULL "
        "AND created_at >= DATE_SUB(NOW(), INTERVAL %s HOUR) "
        "ORDER BY created_at DESC LIMIT 1",
        (spec_id, phash, max_age_hours),
    )


def save(*, user_id: int, spec_id: str, title: str, params: Dict[str, Any],
         payload: Dict[str, Any], html: str, question: str = "",
         cache_key: Optional[str] = None) -> int:
    """cache_key: 캐시 조회에 쓸 해시.

    ⚠️ 품질 게이트가 채널을 제외하면 2회차 파라미터가 달라진다. 최종 파라미터로 해시를 만들면
       다음 요청(1회차 파라미터)과 영영 어긋나 **캐시가 절대 히트하지 않는다** (2026-08-12 실측).
       그래서 **요청 시점의 파라미터**로 만든 해시를 받아서 저장한다.
    """
    ensure_report_tables()
    phash = cache_key or params_hash(spec_id, params)
    gates_failed = sum(1 for g in payload.get("gates", []) if not g.get("passed"))

    rid = execute_lastid(
        "INSERT INTO reports (user_id, spec, title, params_hash, params_json, "
        "payload_json, question, gates_failed, elapsed_sec) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (user_id, spec_id, title, phash,
         json.dumps(params, ensure_ascii=False, default=str),
         json.dumps(payload, ensure_ascii=False, default=str),
         question[:1000], gates_failed,
         (payload.get("meta") or {}).get("elapsed_sec")),
    )

    os.makedirs(REPORT_DIR, exist_ok=True)
    path = os.path.join(REPORT_DIR, f"{rid}.html")
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(html)
    execute("UPDATE reports SET html_path = %s WHERE id = %s", (path, rid))

    logger.info("report_saved", report_id=rid, spec=spec_id, user_id=user_id,
                gates_failed=gates_failed)
    return rid


# 이름은 AD 한글 이름을 우선한다 (users.display_name 은 가입 시점 값이라 낡을 수 있다)
_NAME = "COALESCE(a.display_name, u.display_name, u.email)"


def get_for_user(report_id: int, user_id: int) -> Optional[Dict[str, Any]]:
    """이 사용자가 열 수 있는 보고서. 아니면 None.

    **열람 판정은 여기 한 곳이다** — 소유자이거나, 소유자가 지목해 공유한 사람이거나.
    없는 것과 권한 없는 것을 구분해 알려주지 않는다 (존재 여부도 정보다).
    """
    row = fetch_one(
        f"SELECT r.id, r.user_id, r.spec, r.title, r.params_json, r.payload_json, "
        f"r.html_path, r.question, r.gates_failed, r.created_at, "
        f"{_NAME} AS owner_name "
        "FROM reports r "
        "JOIN users u ON u.id = r.user_id "
        "LEFT JOIN ad_users a ON a.id = u.ad_user_id "
        "LEFT JOIN report_shares s ON s.report_id = r.id AND s.user_id = %s "
        "WHERE r.id = %s AND (r.user_id = %s OR s.id IS NOT NULL)",
        (user_id, report_id, user_id),
    )
    if row:
        row["is_owner"] = int(row.get("user_id") or 0) == int(user_id)
    return row


def list_for_user(user_id: int, limit: int = 30) -> List[Dict[str, Any]]:
    """내가 만든 것 + 나에게 공유된 것. 어느 쪽인지 `is_owner` 로 구분한다."""
    return fetch_all(
        f"SELECT r.id, r.spec, r.title, r.question, r.gates_failed, r.elapsed_sec, "
        f"r.created_at, (r.user_id = %s) AS is_owner, {_NAME} AS owner_name, "
        "(SELECT COUNT(*) FROM report_shares x WHERE x.report_id = r.id) AS share_count "
        "FROM reports r "
        "JOIN users u ON u.id = r.user_id "
        "LEFT JOIN ad_users a ON a.id = u.ad_user_id "
        "LEFT JOIN report_shares s ON s.report_id = r.id AND s.user_id = %s "
        "WHERE r.user_id = %s OR s.id IS NOT NULL "
        "ORDER BY r.created_at DESC LIMIT %s",
        (user_id, user_id, user_id, limit),
    )


# ── 공유 ─────────────────────────────────────────────────────────────────────

def share_list(report_id: int, owner_id: int) -> List[Dict[str, Any]]:
    """누구에게 공유돼 있나. **소유자에게만** 보여준다."""
    return fetch_all(
        f"SELECT s.user_id, {_NAME} AS name, COALESCE(a.email, u.email) AS email, "
        "COALESCE(a.department, '') AS department, s.created_at "
        "FROM report_shares s "
        "JOIN reports r ON r.id = s.report_id AND r.user_id = %s "
        "JOIN users u ON u.id = s.user_id "
        "LEFT JOIN ad_users a ON a.id = u.ad_user_id "
        "WHERE s.report_id = %s ORDER BY s.created_at",
        (owner_id, report_id),
    )


def share_add(report_id: int, owner_id: int, target_id: int) -> bool:
    """공유를 건다. 소유자가 아니면 아무 일도 일어나지 않는다.

    ⚠️ 소유자 확인을 `SELECT ... WHERE r.user_id = owner` 로 **INSERT 안에서** 한다.
       파이썬에서 미리 확인하는 방식이면 확인을 빠뜨린 호출부가 언젠가 생긴다.
    """
    if int(target_id) == int(owner_id):
        return False        # 자기 자신에게 거는 공유는 의미가 없다
    ensure_report_tables()
    n = execute(
        "INSERT IGNORE INTO report_shares (report_id, user_id, shared_by) "
        "SELECT r.id, u.id, r.user_id FROM reports r JOIN users u ON u.id = %s "
        "WHERE r.id = %s AND r.user_id = %s",
        (target_id, report_id, owner_id),
    )
    ok = bool(n)
    logger.info("report_share_add", report_id=report_id, owner_id=owner_id,
                target_id=target_id, created=ok)
    return ok


def share_remove(report_id: int, owner_id: int, target_id: int) -> bool:
    n = execute(
        "DELETE s FROM report_shares s JOIN reports r ON r.id = s.report_id "
        "WHERE s.report_id = %s AND s.user_id = %s AND r.user_id = %s",
        (report_id, target_id, owner_id),
    )
    logger.info("report_share_remove", report_id=report_id, owner_id=owner_id,
                target_id=target_id, removed=bool(n))
    return bool(n)


def search_share_targets(q: str, me_id: int, limit: int = 8) -> List[Dict[str, Any]]:
    """공유할 사람 찾기. **가입한 사용자만** 나온다 — 로그인해야 열 수 있기 때문이다.

    미가입자에게 미리 권한을 걸어두는 FI 방식([[ad_users]])과 다른 이유: 공유는 지금
    보여주려고 누르는 동작이라, 열 수 없는 사람을 목록에 올리면 공유한 줄 알게 된다.
    """
    term = (q or "").strip()
    if len(term) < 2:       # 한 글자면 전 직원 명부를 훑는 것과 다름없다
        return []
    like = f"%{term}%"
    return fetch_all(
        f"SELECT u.id, {_NAME} AS name, COALESCE(a.email, u.email) AS email, "
        "COALESCE(a.department, '') AS department "
        "FROM users u LEFT JOIN ad_users a ON a.id = u.ad_user_id "
        "WHERE u.id <> %s AND (a.display_name LIKE %s OR u.display_name LIKE %s "
        "  OR u.email LIKE %s OR a.email LIKE %s OR a.department LIKE %s) "
        f"ORDER BY {_NAME} LIMIT %s",
        (me_id, like, like, like, like, like, limit),
    )


def read_html(row: Dict[str, Any]) -> Optional[str]:
    """저장된 HTML. 파일이 없으면 payload 로 다시 렌더한다."""
    path = row.get("html_path")
    if path and os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            return fh.read()

    if not row.get("payload_json"):
        return None
    try:
        from app.reports import render
        from app.reports.registry import get_spec
        payload = json.loads(row["payload_json"])
        spec = get_spec(row["spec"])
        return render.render(payload, spec.template, allow_literals=spec.allow_literals)
    except Exception as e:
        logger.warning("report_rerender_failed", report_id=row.get("id"), error=str(e)[:200])
        return None


def purge_older_than(days: int = 90) -> int:
    """오래된 보고서 정리. 원가·마진이 든 파일을 무기한 쌓아두지 않는다."""
    rows = fetch_all(
        "SELECT id, html_path FROM reports WHERE created_at < DATE_SUB(NOW(), INTERVAL %s DAY)",
        (days,),
    )
    for r in rows:
        p = r.get("html_path")
        if p and os.path.exists(p):
            try:
                os.remove(p)
            except OSError:
                pass
    if rows:
        # 공유 행을 먼저 지운다 — 남으면 나중에 같은 id 가 재사용될 때 남의 것이 열린다
        execute("DELETE s FROM report_shares s LEFT JOIN reports r ON r.id = s.report_id "
                "WHERE r.id IS NULL OR r.created_at < DATE_SUB(NOW(), INTERVAL %s DAY)", (days,))
        execute("DELETE FROM reports WHERE created_at < DATE_SUB(NOW(), INTERVAL %s DAY)", (days,))
    return len(rows)


# ── 알림 (공유받은 보고서) ───────────────────────────────────────────────────
#
# 알림은 저장되는 것이 아니라 **파생값**이다 — "나에게 공유됐는데 아직 안 본 것".
# 그래서 공유를 해제하면 알림도 함께 사라진다 (행이 없어지므로).

def unseen_count(user_id: int) -> int:
    row = fetch_one(
        "SELECT COUNT(*) AS c FROM report_shares s JOIN reports r ON r.id = s.report_id "
        "WHERE s.user_id = %s AND s.seen_at IS NULL",
        (user_id,))
    return int(row["c"]) if row else 0


def list_notifications(user_id: int, limit: int = 30) -> List[Dict[str, Any]]:
    """내게 온 알림만. 판정은 서버에서 user_id 로 한다 (프론트 값을 믿지 않는다)."""
    return fetch_all(
        f"SELECT s.report_id, s.created_at, s.seen_at, r.title, r.question, "
        f"{_NAME} AS from_name "
        "FROM report_shares s "
        "JOIN reports r ON r.id = s.report_id "
        "JOIN users u ON u.id = s.shared_by "
        "LEFT JOIN ad_users a ON a.id = u.ad_user_id "
        "WHERE s.user_id = %s ORDER BY s.created_at DESC LIMIT %s",
        (user_id, limit),
    )


def mark_seen(report_id: int, user_id: int) -> bool:
    """그 사람이 그 보고서를 **열었을 때** 읽음으로 만든다.

    목록에서 눌렀든 채팅 링크로 열었든 주소를 직접 쳤든 같은 자리에서 처리한다 —
    읽음 처리를 여러 곳에 흩으면 한 경로에서만 배지가 안 사라진다.
    """
    n = execute(
        "UPDATE report_shares SET seen_at = NOW() "
        "WHERE report_id = %s AND user_id = %s AND seen_at IS NULL",
        (report_id, user_id))
    return bool(n)


def mark_all_seen(user_id: int) -> int:
    return int(execute(
        "UPDATE report_shares SET seen_at = NOW() "
        "WHERE user_id = %s AND seen_at IS NULL", (user_id,)) or 0)
