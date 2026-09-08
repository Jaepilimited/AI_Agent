# -*- coding: utf-8 -*-
"""출근 브리핑의 노션 전달 — 사용자별 대상 페이지 + 발송 대기열.

**왜 잔디와 대기열을 나누나.** 잔디 대기열은 DB_PC 릴레이가 `pending()` 으로 꺼내
간다 — 한 테이블에 채널만 더하면 릴레이가 노션 건을 집어 웹훅으로 쏜다. 노션은
WAS 가 프록시를 통해 직접 쓰므로 릴레이가 아예 없다.

⛔ 절 목록·절 끄기·도착 시각 규칙은 **잔디 것을 그대로 쓴다.** 사본을 만들면
   한쪽만 고쳐져 조용히 갈린다 (이 저장소가 반복해서 겪은 실패다).
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

import structlog

from app.core import notion_export as nx
from app.core.jandi_briefing import (  # noqa: F401  (재수출 — 사본 금지)
    DEFAULT_SEND_AT,
    SECTIONS,
    SECTION_KEYS,
    SEND_TIME_CHOICES,
    BRIEFING_SECTION_KEYS,
    normalize_send_at,
    now_kst,
    parse_muted,
    send_after_for,
    serialize_muted,
    wants,
)
from app.db.mariadb import execute, fetch_all, fetch_one

logger = structlog.get_logger(__name__)

MAX_ATTEMPTS = 3

_TARGET_DDL = """
CREATE TABLE IF NOT EXISTS user_notion_targets (
    user_id INT NOT NULL PRIMARY KEY,
    page_url VARCHAR(500) NOT NULL,
    enabled TINYINT NOT NULL DEFAULT 1,
    send_at TIME NOT NULL DEFAULT '08:00:00',
    muted_sections VARCHAR(255) NOT NULL DEFAULT '',
    last_sent_at DATETIME NULL,
    last_error VARCHAR(255) NOT NULL DEFAULT '',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""

_OUTBOX_DDL = """
CREATE TABLE IF NOT EXISTS briefing_notion_outbox (
    id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL,
    for_date DATE NOT NULL,
    dedup_key VARCHAR(120) NOT NULL DEFAULT '',
    title VARCHAR(200) NOT NULL DEFAULT '',
    page_url VARCHAR(500) NOT NULL,
    body MEDIUMTEXT NOT NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'pending',
    send_after DATETIME NULL,
    attempts INT NOT NULL DEFAULT 0,
    last_error VARCHAR(255) NOT NULL DEFAULT '',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    sent_at DATETIME NULL,
    UNIQUE KEY uniq_user_item (user_id, dedup_key),
    INDEX idx_notion_outbox_status (status, id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""


def ensure_tables() -> None:
    for statement in (_TARGET_DDL, _OUTBOX_DDL):
        try:
            execute(statement)
        except Exception as exc:                    # 기동을 막지 않는다
            logger.warning("notion_briefing_table_error", error=str(exc)[:160])


def is_valid_page_url(url: str) -> bool:
    """⛔ 아무 URL 이나 받지 않는다 — 노션 주소로 읽히는 것만.

    판정은 1단계 엔진의 `parse_page_url` 에 맡긴다 (`*.notion.site` 거절 포함).
    """
    return bool(nx.parse_page_url(url))


def mask(url: str) -> str:
    """화면에 되돌릴 때 쓰는 가림 표기. 페이지 id 를 다시 내보내지 않는다."""
    page_id = nx.parse_page_url(url)
    if not page_id:
        return ""
    return f"notion.so/…{page_id[-4:]}"


def get_target(user_id: int) -> dict[str, Any] | None:
    return fetch_one(
        "SELECT user_id, page_url, enabled, send_at, muted_sections, "
        "last_sent_at, last_error FROM user_notion_targets WHERE user_id=%s",
        (int(user_id),))


def set_target(user_id: int, page_url: str, enabled: bool = True,
               send_at: Any = None, muted: Any = None) -> None:
    """⚠️ `muted` 가 None 이면 **바꾸지 않는다** — 시각만 저장하는 요청이
       항목 설정을 지우면 안 된다. 빈 목록(`[]`)은 "전부 받기" 라는 뜻이다.
    """
    # ⛔ `normalize_send_at(None)` 은 ValueError 다 — 시각을 안 주고 등록하는
    #    흐름(첫 등록·채팅 등록)이 그대로 죽는다. 잔디의 `set_webhook` 과 같은 가드다.
    when = DEFAULT_SEND_AT if send_at is None else normalize_send_at(send_at)
    execute(
        "INSERT INTO user_notion_targets "
        "(user_id, page_url, enabled, send_at, muted_sections) "
        "VALUES (%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE "
        "page_url=VALUES(page_url), enabled=VALUES(enabled), "
        "send_at=VALUES(send_at), "
        "muted_sections=IF(%s, VALUES(muted_sections), muted_sections)",
        (int(user_id), page_url.strip(), 1 if enabled else 0,
         when, serialize_muted(muted or []),
         1 if muted is not None else 0),
    )


def delete_target(user_id: int) -> None:
    execute("DELETE FROM user_notion_targets WHERE user_id=%s", (int(user_id),))


def enabled_recipients() -> list[dict[str, Any]]:
    return fetch_all(
        "SELECT user_id, page_url, send_at, muted_sections "
        "FROM user_notion_targets WHERE enabled=1")
