# -*- coding: utf-8 -*-
"""비밀번호 재설정 **요청 접수** — 로그인 화면에서 본인이 남기고, 관리자가 초기화한다.

**왜 접수만인가** (2026-08-31 사용자 결정): 셀프 재설정을 하려면 본인 확인 수단이
있어야 하는데 실측해 보니 쓸 만한 것이 없었다.

    메일로 재설정 링크   ❌ WAS·APP 양쪽 SMTP 25/587 차단, 로컬 MTA 없음, Gmail 스코프 readonly
    AD(Windows) 확인     ❌ WAS 에서 LDAP 미도달 + 회사 비밀번호를 웹폼에 넣게 하는 흐름
    구글 계정 확인       △ 가입자 57명 중 이메일 보유 39명(68%) — 18명이 남는다
    잔디                 ❌ 웹훅은 로그인 후 등록이고 토픽 주소라 신원 증명이 아니다

⛔ **부서·이름만으로 재설정되게 하면 안 된다.** 그 둘은 로그인 화면이 이미 목록으로
   보여 준다 — 누구나 남의 계정을 초기화할 수 있게 되고, 셀라에는 재무 손익(FI)
   데이터가 있다. 그래서 요청은 **계정에 아무 변화도 주지 않는다**. 사람이 판단한다.

⚠️ 이 엔드포인트는 **로그인 없이** 열려 있다. 그래서 두 가지를 코드가 막는다:
     1. 같은 사람 앞으로 이미 대기 중인 요청이 있으면 새로 만들지 않는다 (도배 방지)
     2. 사유는 길이를 자르고 저장한다 (관리자 화면에 그대로 뜬다)
   ⛔ 존재 여부를 새로 노출하지는 않는다 — 부서·이름 목록은 로그인 화면이 이미 준다.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import structlog

from app.db.mariadb import execute, fetch_all, fetch_one

logger = structlog.get_logger(__name__)

#: 사유는 관리자 화면에 그대로 뜬다. 길면 자른다.
MAX_NOTE = 200

#: 한 사람 앞으로 동시에 대기할 수 있는 요청 수. 도배를 막는다.
MAX_OPEN_PER_USER = 1

_DDL = """
CREATE TABLE IF NOT EXISTS password_reset_requests (
    id INT AUTO_INCREMENT PRIMARY KEY,
    ad_user_id INT NOT NULL,
    note VARCHAR(255) NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'open',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    handled_at DATETIME NULL,
    handled_by INT NULL,
    INDEX idx_status (status, created_at),
    INDEX idx_ad_user (ad_user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""


def ensure_password_reset_table() -> None:
    try:
        execute(_DDL)
    except Exception as e:
        logger.warning("password_reset_table_error", error=str(e)[:160])


def create(ad_user_id: int, note: str = "") -> Dict[str, Any]:
    """요청을 남긴다. **계정은 건드리지 않는다.**

    돌려주는 `duplicate` 가 True 면 이미 대기 중이라는 뜻이다 — 화면에는 둘 다
    같은 문구를 보여 준다. "이미 요청했다" 를 알려 주는 편이 다시 누르는 것보다 낫다.
    """
    ensure_password_reset_table()
    open_rows = fetch_all(
        "SELECT id FROM password_reset_requests "
        "WHERE ad_user_id = %s AND status = 'open'", (int(ad_user_id),)) or []
    if len(open_rows) >= MAX_OPEN_PER_USER:
        return {"ok": True, "duplicate": True, "request_id": open_rows[0]["id"]}

    execute("INSERT INTO password_reset_requests (ad_user_id, note) VALUES (%s, %s)",
            (int(ad_user_id), (note or "").strip()[:MAX_NOTE] or None))
    row = fetch_one("SELECT id FROM password_reset_requests "
                    "WHERE ad_user_id = %s ORDER BY id DESC LIMIT 1", (int(ad_user_id),))
    return {"ok": True, "duplicate": False, "request_id": (row or {}).get("id")}


def open_requests(limit: int = 50) -> List[Dict[str, Any]]:
    """대기 중인 요청. 관리자 화면과 잔디 알림이 함께 쓴다."""
    ensure_password_reset_table()
    return fetch_all(
        "SELECT r.id, r.ad_user_id, r.note, r.created_at, "
        "       a.display_name, a.department, "
        "       (u.id IS NOT NULL) AS registered "
        "FROM password_reset_requests r "
        "JOIN ad_users a ON a.id = r.ad_user_id "
        "LEFT JOIN users u ON u.ad_user_id = a.id "
        "WHERE r.status = 'open' ORDER BY r.created_at LIMIT %s", (int(limit),)) or []


def close_for_ad_user(ad_user_id: int, handled_by: Optional[int] = None) -> int:
    """그 사람의 대기 요청을 닫는다.

    ⛔ **초기화와 같은 흐름에서 닫아야 한다.** 따로 두면 관리자가 초기화만 하고
       요청은 계속 대기로 남아, 다음에 볼 때도 맨 앞에 뜬다 — 붐따가 정확히
       그렇게 36건 쌓였다 (CLAUDE.md 붐따 규칙).
    """
    ensure_password_reset_table()
    try:
        execute("UPDATE password_reset_requests SET status = 'done', "
                "handled_at = NOW(), handled_by = %s "
                "WHERE ad_user_id = %s AND status = 'open'",
                (handled_by, int(ad_user_id)))
    except Exception as e:
        logger.warning("password_reset_close_failed", error=str(e)[:160])
        return 0
    return 1


def summary() -> Dict[str, int]:
    ensure_password_reset_table()
    row = fetch_one("SELECT COUNT(*) c FROM password_reset_requests WHERE status='open'")
    return {"open": int((row or {}).get("c") or 0)}
