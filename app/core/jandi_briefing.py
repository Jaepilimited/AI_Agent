"""출근 브리핑의 잔디 전달 — 사용자별 웹훅 + 발송 대기열.

**왜 대기열인가.** 서버(WAS/APP)는 프록시에서 `wh.jandi.com` 이 막혀 있다
(2026-08-18 실측, 양쪽 다 403). 반면 DB_PC(172.16.1.250)는 잔디에 붙고,
DB_PC → 서버는 SSH(:22)만 열려 있다 (:80/:3000/:8000 전부 timeout, 2026-08-25 실측).
그래서 **서버는 만들어 두기만 하고, 발송은 DB_PC 릴레이가 SSH 터널로 꺼내 간다.**

⛔ 잔디 인커밍 웹훅은 DM 이 아니라 **토픽** 주소다. 개인 브리핑이므로 받는 사람이
   각자 자기 토픽의 웹훅을 등록해야 한다 — 공용 URL 하나로 돌리면 남의 메일 제목이
   단체 토픽에 뿌려진다.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

from app.db.mariadb import execute, fetch_all, fetch_one

#: 잔디 인커밍 웹훅만 허용한다. 임의 URL 을 받으면 서버가 만든 개인 메일 요약을
#: 아무 데나 보내주는 발송기가 된다 (SSRF 계열).
WEBHOOK_PATTERN = re.compile(
    r"^https://wh\.jandi\.com/connect-api/webhook/\d+/[A-Za-z0-9_-]+$"
)

MAX_ATTEMPTS = 3

_WEBHOOK_DDL = """
CREATE TABLE IF NOT EXISTS user_jandi_webhooks (
    user_id INT NOT NULL PRIMARY KEY,
    webhook_url VARCHAR(500) NOT NULL,
    enabled TINYINT NOT NULL DEFAULT 1,
    last_sent_at DATETIME NULL,
    last_error VARCHAR(255) NOT NULL DEFAULT '',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""

_OUTBOX_DDL = """
CREATE TABLE IF NOT EXISTS briefing_jandi_outbox (
    id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL,
    for_date DATE NOT NULL,
    kind VARCHAR(24) NOT NULL DEFAULT 'briefing',
    dedup_key VARCHAR(120) NOT NULL DEFAULT '',
    title VARCHAR(200) NOT NULL DEFAULT '',
    link VARCHAR(300) NOT NULL DEFAULT '',
    webhook_url VARCHAR(500) NOT NULL,
    body MEDIUMTEXT NOT NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'pending',
    attempts INT NOT NULL DEFAULT 0,
    last_error VARCHAR(255) NOT NULL DEFAULT '',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    sent_at DATETIME NULL,
    UNIQUE KEY uniq_user_item (user_id, dedup_key),
    INDEX idx_outbox_status (status, id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""

#: 이미 만들어진 테이블에는 CREATE 문이 닿지 않는다.
#: ⚠️ 유니크 키가 (user_id, for_date) 에서 (user_id, dedup_key) 로 바뀐다 —
#:    옛 키를 남겨 두면 하루 한 건 제약이 그대로 살아 알림이 조용히 안 나간다.
_OUTBOX_MIGRATIONS = (
    "ALTER TABLE briefing_jandi_outbox ADD COLUMN kind VARCHAR(24) NOT NULL DEFAULT 'briefing'",
    "ALTER TABLE briefing_jandi_outbox ADD COLUMN dedup_key VARCHAR(120) NOT NULL DEFAULT ''",
    "ALTER TABLE briefing_jandi_outbox ADD COLUMN title VARCHAR(200) NOT NULL DEFAULT ''",
    "ALTER TABLE briefing_jandi_outbox ADD COLUMN link VARCHAR(300) NOT NULL DEFAULT ''",
    "UPDATE briefing_jandi_outbox SET dedup_key = CONCAT('briefing:', for_date) "
    "WHERE dedup_key = ''",
    "ALTER TABLE briefing_jandi_outbox DROP INDEX uniq_user_day",
    "ALTER TABLE briefing_jandi_outbox ADD UNIQUE KEY uniq_user_item (user_id, dedup_key)",
)


def ensure_tables() -> None:
    execute(_WEBHOOK_DDL)
    execute(_OUTBOX_DDL)
    for statement in _OUTBOX_MIGRATIONS:
        try:
            execute(statement)
        except Exception:
            # 이미 적용된 것이다. 없을 때만 의미가 있고, 실패해도 기존 기능은 산다.
            pass


def is_valid_webhook(url: str) -> bool:
    return bool(WEBHOOK_PATTERN.match((url or "").strip()))


def mask(url: str) -> str:
    """화면에 되돌려줄 때 쓰는 가림 표기. 토큰 전체를 다시 내보내지 않는다."""

    text = (url or "").strip()
    if not text:
        return ""
    tail = text.rsplit("/", 1)[-1]
    return f"…/{tail[:4]}{'*' * 8}" if len(tail) > 4 else "…"


# ── 사용자별 웹훅 ────────────────────────────────────────────────────────────

def get_webhook(user_id: int) -> dict[str, Any] | None:
    return fetch_one(
        "SELECT user_id,webhook_url,enabled,last_sent_at,last_error "
        "FROM user_jandi_webhooks WHERE user_id = %s",
        (int(user_id),),
    )


def set_webhook(user_id: int, url: str, enabled: bool = True) -> None:
    """저장 전에 형식을 검증한다 — 호출부가 빠뜨려도 여기서 막힌다."""

    clean = (url or "").strip()
    if not is_valid_webhook(clean):
        raise ValueError("jandi webhook url is not a wh.jandi.com connect-api address")
    execute(
        "INSERT INTO user_jandi_webhooks (user_id,webhook_url,enabled,last_error) "
        "VALUES (%s,%s,%s,'') "
        "ON DUPLICATE KEY UPDATE webhook_url=VALUES(webhook_url),"
        "enabled=VALUES(enabled),last_error=''",
        (int(user_id), clean, 1 if enabled else 0),
    )


def delete_webhook(user_id: int) -> None:
    execute("DELETE FROM user_jandi_webhooks WHERE user_id = %s", (int(user_id),))
    execute(
        "DELETE FROM briefing_jandi_outbox WHERE user_id = %s AND status = 'pending'",
        (int(user_id),),
    )


def drop_pending_for_user(user_id: int) -> int:
    """구글 연결을 끊으면 아직 안 보낸 본문도 버린다 — 그 안에 끊은 계정의 메일 요약이 있다.

    등록한 웹훅은 남긴다 (다시 연결하면 그대로 받는다).
    """

    return int(
        execute(
            "DELETE FROM briefing_jandi_outbox WHERE user_id = %s AND status = 'pending'",
            (int(user_id),),
        )
        or 0
    )


def enabled_recipients() -> list[dict[str, Any]]:
    return fetch_all(
        "SELECT w.user_id,w.webhook_url FROM user_jandi_webhooks w "
        "JOIN users u ON u.id = w.user_id "
        "WHERE w.enabled = 1 AND u.is_active = 1",
    )


# ── 발송 대기열 ──────────────────────────────────────────────────────────────

def enqueue(user_id: int, for_date: date, webhook_url: str, body: str,
            kind: str = "briefing", dedup_key: str = "",
            title: str = "", link: str = "") -> bool:
    """한 건 넣는다. **같은 `dedup_key` 는 두 번 들어가지 않는다.**

    ⛔ 같은 알림을 두 번 보내면 그 다음부터 아무도 안 읽는다 — 브리핑이 '하루 한 건'
       이던 이유와 같다. 브리핑의 키는 날짜이고, 알림의 키는 그 항목 자신이다.
    """

    if not body.strip() or not is_valid_webhook(webhook_url):
        return False
    key = (dedup_key or f"{kind}:{for_date}")[:120]
    changed = execute(
        "INSERT INTO briefing_jandi_outbox "
        "(user_id,for_date,kind,dedup_key,title,link,webhook_url,body) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s) "
        "ON DUPLICATE KEY UPDATE "
        "webhook_url=VALUES(webhook_url),"
        # 이미 보낸 건은 본문을 바꾸지 않는다 (다시 보내지 않으므로 의미도 없다).
        "body=IF(status='pending',VALUES(body),body),"
        "title=IF(status='pending',VALUES(title),title),"
        "link=IF(status='pending',VALUES(link),link)",
        (int(user_id), for_date, kind[:24], key, title[:200], link[:300],
         webhook_url, body),
    )
    return bool(changed)


def pending(limit: int = 50) -> list[dict[str, Any]]:
    return fetch_all(
        "SELECT id,user_id,for_date,kind,title,link,webhook_url,body,attempts "
        "FROM briefing_jandi_outbox "
        "WHERE status = 'pending' AND attempts < %s ORDER BY id LIMIT %s",
        (MAX_ATTEMPTS, int(limit)),
    )


def mark_sent(outbox_id: int) -> None:
    execute(
        "UPDATE briefing_jandi_outbox SET status='sent',sent_at=NOW(),last_error='' "
        "WHERE id = %s",
        (int(outbox_id),),
    )
    execute(
        "UPDATE user_jandi_webhooks w "
        "JOIN briefing_jandi_outbox o ON o.user_id = w.user_id "
        "SET w.last_sent_at = NOW(), w.last_error = '' WHERE o.id = %s",
        (int(outbox_id),),
    )


def mark_failed(outbox_id: int, error: str) -> None:
    """시도 횟수를 올리고, 한계에 닿으면 실패로 굳힌다 — 무한 재시도를 만들지 않는다."""

    execute(
        "UPDATE briefing_jandi_outbox SET attempts = attempts + 1, last_error = %s, "
        "status = IF(attempts + 1 >= %s, 'failed', 'pending') WHERE id = %s",
        (str(error)[:255], MAX_ATTEMPTS, int(outbox_id)),
    )
    execute(
        "UPDATE user_jandi_webhooks w "
        "JOIN briefing_jandi_outbox o ON o.user_id = w.user_id "
        "SET w.last_error = %s WHERE o.id = %s",
        (str(error)[:255], int(outbox_id)),
    )


def status_counts(for_date: date | None = None) -> dict[str, int]:
    """자가 점검·Admin 이 읽는 요약. 대기가 쌓이면 릴레이가 안 도는 것이다."""

    if for_date is None:
        rows = fetch_all(
            "SELECT status, COUNT(*) c FROM briefing_jandi_outbox "
            "WHERE created_at >= DATE_SUB(NOW(), INTERVAL 7 DAY) GROUP BY status",
        )
    else:
        rows = fetch_all(
            "SELECT status, COUNT(*) c FROM briefing_jandi_outbox "
            "WHERE for_date = %s GROUP BY status",
            (for_date,),
        )
    counts = {"pending": 0, "sent": 0, "failed": 0}
    for row in rows:
        counts[str(row["status"])] = int(row["c"])
    return counts


def cleanup(before: date) -> int:
    return int(
        execute("DELETE FROM briefing_jandi_outbox WHERE for_date < %s", (before,)) or 0
    )


#: 종류별 표시. 잔디에서는 색과 제목만으로 무엇인지 알아야 한다.
KIND_META = {
    "briefing": ("오늘의 출근 브리핑", "#f5a623"),
    "report_share": ("보고서가 공유되었습니다", "#4b8bf5"),
    "feedback": ("보내주신 의견이 처리되었습니다", "#46be8a"),
    "announcement": ("공지", "#9b6bde"),
}


def jandi_payload(body: str, kind: str = "briefing", title: str = "",
                  link: str = "") -> dict[str, Any]:
    """잔디 커넥트 형식. 잔디는 마크다운을 거의 그리지 않으므로 본문은 평문이다.

    ⚠️ **링크를 항상 붙인다.** 잔디에서 읽고 끝나면 셀라에 오지 않는다 —
       실측(2026-08-26) 브리핑 열람률 8.1%, 그런데 열어본 날 질문 전환은 23.7%였다.
       도달이 병목이므로 '여기서 이어서 물어보기' 로 가는 문을 매번 열어 둔다.
    """

    label, color = KIND_META.get(kind, KIND_META["briefing"])
    info = [{"title": title or label,
             "description": datetime.now().strftime("%Y-%m-%d %H:%M")}]
    if link:
        info.append({"title": "셀라에서 이어서 물어보기", "description": link})
    return {"body": body[:9000], "connectColor": color, "connectInfo": info}
