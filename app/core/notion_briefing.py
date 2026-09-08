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
    row_url VARCHAR(300) NOT NULL DEFAULT '',
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


def enqueue(user_id: int, for_date: date, page_url: str, body: str,
            title: str, send_after: datetime | None = None) -> bool:
    """하루치 브리핑 한 건을 넣는다. **같은 날짜는 두 번 들어가지 않는다.**"""
    if not body.strip() or not is_valid_page_url(page_url):
        return False
    key = f"briefing:{for_date}"[:120]
    changed = execute(
        "INSERT INTO briefing_notion_outbox "
        "(user_id,for_date,dedup_key,title,page_url,body,send_after) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s) "
        "ON DUPLICATE KEY UPDATE "
        "page_url=IF(status='pending',VALUES(page_url),page_url),"
        # 이미 보낸 건은 본문을 바꾸지 않는다 (다시 보내지 않으므로 뜻도 없다).
        "body=IF(status='pending',VALUES(body),body),"
        "title=IF(status='pending',VALUES(title),title),"
        "send_after=IF(status='pending',VALUES(send_after),send_after)",
        (int(user_id), for_date, key, title[:200], page_url.strip(), body,
         send_after),
    )
    return bool(changed)


def pending(now: datetime, limit: int = 50) -> list[dict[str, Any]]:
    """도착 시각이 된 것만 꺼낸다.

    ⛔ `NOW()` 로 비교하지 마라 — DB `time_zone` 이 `SYSTEM` 이라 호스트 TZ 가
       바뀌면 9시간 어긋난다. 시계는 **파라미터로** 받는다.
    ⚠️ `send_after IS NULL` 도 함께 꺼낸다 — 즉시 발송분이 그렇다.
    """
    return fetch_all(
        "SELECT id,user_id,for_date,title,page_url,body,attempts "
        "FROM briefing_notion_outbox "
        "WHERE status='pending' AND attempts < %s "
        "AND (send_after IS NULL OR send_after <= %s) "
        "ORDER BY id LIMIT %s",
        (MAX_ATTEMPTS, now, int(limit)))


def mark_sent(outbox_id: int, row_url: str) -> None:
    """⚠️ 만들어진 노션 행 주소는 `row_url` 에 남긴다 — `last_error` 에 성공 값을
       넣으면 나중에 읽는 사람이 반드시 오해한다. 성공했으니 오류는 비운다.
    """
    execute(
        "UPDATE briefing_notion_outbox "
        "SET status='sent', sent_at=NOW(), row_url=%s, last_error='' WHERE id=%s",
        (row_url[:300], int(outbox_id)))


def mark_failed(outbox_id: int, error: str) -> None:
    """시도 횟수를 올리고, 상한에 닿으면 실패로 굳힌다 — 무한 재시도를 만들지 않는다.

    ⛔ **SET 절 순서가 의미를 바꾼다.** MySQL 은 단일 테이블 UPDATE 의 SET 을
       왼쪽부터 평가하고 **뒤 절이 앞 절의 새 값을 본다.** `attempts=attempts+1` 을
       먼저 쓰면 `status` 절이 이미 증가한 값을 읽어 `old+2 >= 상한` 을 검사하게 되고,
       재시도가 한 번 일찍 끝난다. 그래서 `status` 를 **먼저** 계산한다.
       ⚠️ 보기 좋게 정리한다고 순서를 바꾸지 마라.
    """
    execute(
        "UPDATE briefing_notion_outbox "
        "SET status=IF(attempts+1 >= %s,'failed','pending'), "
        "attempts=attempts+1, last_error=%s WHERE id=%s",
        (MAX_ATTEMPTS, error[:255], int(outbox_id)))


def status_counts(for_date: date | None = None) -> dict[str, int]:
    sql = "SELECT status, COUNT(*) AS n FROM briefing_notion_outbox"
    params: tuple = ()
    if for_date is not None:
        sql += " WHERE for_date=%s"
        params = (for_date,)
    sql += " GROUP BY status"
    return {str(row["status"]): int(row["n"]) for row in fetch_all(sql, params)}


def cleanup(before: date) -> int:
    return execute(
        "DELETE FROM briefing_notion_outbox WHERE for_date < %s", (before,))
