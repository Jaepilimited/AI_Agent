# -*- coding: utf-8 -*-
"""업데이트 공지 — "무엇이 바뀌었는지" 를 사용자가 알 수 있는 유일한 경로.

배포는 자주 하는데 사용자는 바뀐 것을 모른다. 초상권 사진 판정처럼 **있는 줄도 모르는
기능**이 실제로 있었다 (2026-08-19: 출시 후 한 번도 안 쓰인 경로가 드러났다).

읽음 판정은 사용자마다 시각 하나(`users.announce_seen_at`)로 한다 — 공지×사용자
교차표를 만들면 사용자 300명 × 공지가 계속 쌓인다. 공지는 순서가 있고 개수가 적어
"언제까지 읽었나" 한 값으로 충분하다.
"""
from __future__ import annotations

from typing import Any, Dict, List

import structlog

from app.db.mariadb import execute, execute_lastid, fetch_all, fetch_one

logger = structlog.get_logger(__name__)

_DDL = """
CREATE TABLE IF NOT EXISTS announcements (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    title       VARCHAR(200) NOT NULL,
    body        TEXT NULL,
    created_by  VARCHAR(120) NOT NULL DEFAULT '',
    created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_created (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""


def ensure_tables() -> None:
    """공지 표 + 사용자별 읽음 시각 (앱 기동 시 idempotent)."""
    try:
        execute(_DDL)
    except Exception as e:
        logger.debug("announcement_ddl_skip", error=str(e)[:120])
    try:
        if not fetch_one(
            "SELECT 1 AS ok FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'users' "
            "AND COLUMN_NAME = 'announce_seen_at'"):
            execute("ALTER TABLE users ADD COLUMN announce_seen_at DATETIME NULL")
            logger.info("users_announce_seen_at_added")
    except Exception as e:
        logger.debug("announce_column_skip", error=str(e)[:120])


def create(title: str, body: str, by: str) -> int:
    ensure_tables()
    new_id = execute_lastid(
        "INSERT INTO announcements (title, body, created_by) VALUES (%s, %s, %s)",
        (title[:200], body or "", by[:120]))
    logger.info("announcement_created", id=new_id, by=by)
    return int(new_id or 0)


def delete(ann_id: int) -> bool:
    return bool(execute("DELETE FROM announcements WHERE id = %s", (int(ann_id),)))


def recent(limit: int = 10) -> List[Dict[str, Any]]:
    return fetch_all(
        "SELECT id, title, body, created_by, created_at FROM announcements "
        "ORDER BY created_at DESC LIMIT %s", (int(limit),)) or []


def for_user(user_id: int, limit: int = 10) -> List[Dict[str, Any]]:
    """공지 목록 + 내가 아직 안 본 것 표시.

    ⚠️ 안 읽음은 **내 읽음 시각 이후에 올라온 것**이다. 시각이 없으면(한 번도 안 봄)
       전부 안 읽음이 아니라 **최근 것만** 뜨도록 목록 자체를 limit 로 줄여 둔다 —
       새로 가입한 사람에게 지난 공지가 배지로 무더기로 뜨면 곧 무시한다.
    """
    rows = recent(limit)
    if not rows:
        return []
    me = fetch_one("SELECT announce_seen_at FROM users WHERE id = %s", (int(user_id),))
    seen_at = (me or {}).get("announce_seen_at")
    for r in rows:
        r["unseen"] = bool(r["created_at"] and (not seen_at or r["created_at"] > seen_at))
    return rows


def mark_seen(user_id: int) -> None:
    execute("UPDATE users SET announce_seen_at = NOW() WHERE id = %s", (int(user_id),))
