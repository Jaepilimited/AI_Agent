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
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.db.mariadb import execute, fetch_all, fetch_one

#: 잔디 인커밍 웹훅만 허용한다. 임의 URL 을 받으면 서버가 만든 개인 메일 요약을
#: 아무 데나 보내주는 발송기가 된다 (SSRF 계열).
WEBHOOK_PATTERN = re.compile(
    r"^https://wh\.jandi\.com/connect-api/webhook/\d+/[A-Za-z0-9_-]+$"
)

MAX_ATTEMPTS = 3

KST = ZoneInfo("Asia/Seoul")

#: 릴레이가 실제로 대기열을 꺼내 가는 회차. DB_PC 예약작업 `SKIN1004-Jandi-Briefing`
#: 이 **08:00 시작 · 30분 간격 · 10h35m** 이라 08:00~18:30 이다.
#: ⛔ 여기 없는 시각을 고르게 두면 그 사람의 브리핑은 **영영 오지 않는다** (에러도 없다).
#: ⚠️ 예약작업 주기를 바꾸면 이 셋을 함께 고칠 것 — 프론트는 이 목록을 받아서 그린다.
RELAY_FIRST_RUN = time(8, 0)
RELAY_LAST_RUN = time(18, 30)
RELAY_INTERVAL_MINUTES = 30


def _relay_runs() -> list[str]:
    start = datetime(2000, 1, 1, RELAY_FIRST_RUN.hour, RELAY_FIRST_RUN.minute)
    end = datetime(2000, 1, 1, RELAY_LAST_RUN.hour, RELAY_LAST_RUN.minute)
    runs = []
    while start <= end:
        runs.append(start.strftime("%H:%M"))
        start += timedelta(minutes=RELAY_INTERVAL_MINUTES)
    return runs


SEND_TIME_CHOICES = _relay_runs()

#: 바꾼 적 없는 사람은 지금과 똑같이 첫 회차에 받는다.
DEFAULT_SEND_AT = RELAY_FIRST_RUN


# ── 무엇을 받을지 (사용자별) ─────────────────────────────────────────────────
#: 잔디로 나가는 것들. `(키, 화면 이름, 묶음)`.
#: ⚠️ 브리핑 절 키는 `work_briefing` 문서의 키와 **같아야** 한다 — 다르면
#:    끈 줄 알았는데 그대로 나간다 (에러 없이).
SECTIONS: tuple[tuple[str, str, str], ...] = (
    ("meetings", "오늘의 일정", "브리핑"),
    ("mail", "수신 메일", "브리핑"),
    ("actions", "우선순위 Action Item", "브리핑"),
    ("deadlines", "마감·기한", "브리핑"),
    ("saved", "내가 저장한 보고", "브리핑"),
    ("business", "업무 지표", "브리핑"),
    ("fx", "오늘의 환율", "브리핑"),
    ("report_share", "보고서 공유 알림", "알림"),
    ("feedback", "의견(붐따) 회신 알림", "알림"),
    ("group_assign", "그룹 배정 대기 알림", "알림"),
)

SECTION_KEYS = frozenset(key for key, _, _ in SECTIONS)

#: 브리핑 본문을 이루는 절만. 이것이 전부 꺼지면 보낼 것이 없다.
BRIEFING_SECTION_KEYS = frozenset(
    key for key, _, group in SECTIONS if group == "브리핑"
)


def parse_muted(value: Any) -> frozenset[str]:
    """저장된 문자열 → 끈 항목 집합. 모르는 키는 조용히 버린다.

    ⛔ **끈 것**을 저장한다 (켠 것이 아니라). 켠 목록으로 두면 나중에 절이 하나
       늘었을 때 **이미 설정을 저장해 둔 사람에게는 영영 안 보인다** — 에러도 없고
       화면에도 흔적이 없다. 끈 목록이면 새 절은 모두에게 기본으로 켜진다.
    """

    if value is None:
        return frozenset()
    if isinstance(value, (set, frozenset, list, tuple)):
        raw = [str(item) for item in value]
    else:
        raw = str(value).split(",")
    return frozenset(item.strip() for item in raw if item.strip() in SECTION_KEYS)


def serialize_muted(value: Any) -> str:
    """집합 → 저장 문자열. SECTIONS 순서로 적어 사람이 읽을 수 있게 둔다."""

    muted = parse_muted(value)
    return ",".join(key for key, _, _ in SECTIONS if key in muted)


def wants(muted: Any, key: str) -> bool:
    """그 사람이 이 항목을 받기로 했는가. 모르는 키는 **받는 쪽**으로 답한다."""

    return key not in parse_muted(muted)


def normalize_send_at(value: Any) -> time:
    """`"09:30"` → `time(9,30)`. 릴레이가 가지 않는 시각은 거부한다.

    DB 는 TIME 을 `datetime.time` 으로도 `"09:30:00"` 으로도 돌려주므로 둘 다 받는다.
    """

    if isinstance(value, time):
        candidate = value.replace(second=0, microsecond=0)
    elif isinstance(value, timedelta):
        # ⚠️ PyMySQL 은 TIME 컬럼을 `timedelta` 로 돌려준다. str() 이 우연히
        #    `8:00:00` 으로 찍혀 통과하지만, 우연에 기대지 않고 여기서 받는다.
        minutes = int(value.total_seconds()) // 60
        candidate = time(minutes // 60 % 24, minutes % 60)
    else:
        text = str(value or "").strip()
        if not re.fullmatch(r"\d{1,2}:\d{2}(:\d{2})?", text):
            raise ValueError(f"send_at must look like HH:MM — got {value!r}")
        hour, minute = (int(part) for part in text.split(":")[:2])
        if hour > 23 or minute > 59:
            raise ValueError(f"send_at is not a real time — got {value!r}")
        candidate = time(hour, minute)
    if candidate.strftime("%H:%M") not in SEND_TIME_CHOICES:
        raise ValueError(
            f"send_at must be one of {SEND_TIME_CHOICES[0]}~{SEND_TIME_CHOICES[-1]} "
            f"in {RELAY_INTERVAL_MINUTES}-minute steps — got {value!r}",
        )
    return candidate


def send_after_for(for_date: date, send_at: Any) -> datetime:
    """그 날짜의 KST 벽시계 시각. `pending()` 이 같은 시계로 견준다."""

    return datetime.combine(for_date, normalize_send_at(send_at))


def now_kst() -> datetime:
    """도착 시각 비교의 기준 시계.

    ⛔ DB 의 `NOW()` 로 견주지 마라. 실측(2026-09-01)하면 DB 시계는 지금 KST 와
       같지만 `time_zone` 이 `SYSTEM` 이다 — **호스트 TZ 를 따라가므로** DB VM 이
       UTC 로 재구축되는 날 9시간 어긋난다. 그때 나는 것은 에러가 아니라
       **브리핑이 조용히 안 가는 것**이라 아무도 모른다. 여기서 만들어 넘기면
       DB 호스트가 무엇이든 판정이 같다.
    """

    return datetime.now(KST).replace(tzinfo=None)


_WEBHOOK_DDL = """
CREATE TABLE IF NOT EXISTS user_jandi_webhooks (
    user_id INT NOT NULL PRIMARY KEY,
    webhook_url VARCHAR(500) NOT NULL,
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
    send_after DATETIME NULL,
    attempts INT NOT NULL DEFAULT 0,
    last_error VARCHAR(255) NOT NULL DEFAULT '',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    sent_at DATETIME NULL,
    UNIQUE KEY uniq_user_item (user_id, dedup_key),
    INDEX idx_outbox_status (status, id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""

#: 이미 만들어진 테이블에는 CREATE 문이 닿지 않는다 — 아래가 그 몫이다.
#: 기본값이 08:00 이라 이미 등록한 사람은 지금과 똑같이 첫 회차에 받는다.
_WEBHOOK_MIGRATIONS = (
    "ALTER TABLE user_jandi_webhooks ADD COLUMN send_at TIME NOT NULL DEFAULT '08:00:00'",
    # 기본값이 빈 문자열이라 이미 등록한 사람은 지금과 똑같이 전부 받는다.
    "ALTER TABLE user_jandi_webhooks ADD COLUMN muted_sections VARCHAR(255) "
    "NOT NULL DEFAULT ''",
)

#: ⚠️ 유니크 키가 (user_id, for_date) 에서 (user_id, dedup_key) 로 바뀐다 —
#:    옛 키를 남겨 두면 하루 한 건 제약이 그대로 살아 알림이 조용히 안 나간다.
_OUTBOX_MIGRATIONS = (
    "ALTER TABLE briefing_jandi_outbox ADD COLUMN send_after DATETIME NULL",
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
    for statement in _WEBHOOK_MIGRATIONS + _OUTBOX_MIGRATIONS:
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
        "SELECT user_id,webhook_url,enabled,send_at,muted_sections,"
        "last_sent_at,last_error "
        "FROM user_jandi_webhooks WHERE user_id = %s",
        (int(user_id),),
    )


def set_webhook(user_id: int, url: str, enabled: bool = True,
                send_at: Any = None, muted: Any = None) -> None:
    """저장 전에 주소와 시각을 함께 검증한다 — 호출부가 빠뜨려도 여기서 막힌다.

    ⛔ 릴레이가 가지 않는 시각을 저장하면 그 사람의 브리핑은 영영 오지 않는다.
       API 에서만 막으면 언젠가 검증을 빠뜨린 호출부가 생긴다.
    """

    clean = (url or "").strip()
    if not is_valid_webhook(clean):
        raise ValueError("jandi webhook url is not a wh.jandi.com connect-api address")
    when = DEFAULT_SEND_AT if send_at is None else normalize_send_at(send_at)
    # ⚠️ `muted=None` 은 "안 바꾼다" 다 (빈 목록 = "전부 받기" 와 뜻이 다르다).
    # ⛔ 읽고-고쳐-쓰지 마라 — 저장 한 번에 조회가 딸려 들어가면 두 요청이 겹쳤을 때
    #    한쪽 설정이 조용히 사라진다. SQL 안에서 고른다 (outbox 의 `body=IF(...)` 와 같다).
    keep = 0 if muted is not None else 1
    muted_text = "" if muted is None else serialize_muted(muted)
    execute(
        "INSERT INTO user_jandi_webhooks "
        "(user_id,webhook_url,enabled,send_at,muted_sections,last_error) "
        "VALUES (%s,%s,%s,%s,%s,'') "
        "ON DUPLICATE KEY UPDATE webhook_url=VALUES(webhook_url),"
        "enabled=VALUES(enabled),send_at=VALUES(send_at),"
        "muted_sections=IF(%s,muted_sections,VALUES(muted_sections)),last_error=''",
        (int(user_id), clean, 1 if enabled else 0, when, muted_text, keep),
    )


def reschedule_pending(user_id: int, send_at: Any) -> int:
    """아직 안 보낸 **브리핑**의 도착 시각을 새로 고른 시각으로 옮긴다.

    ⛔ 이걸 빼면 "고쳤는데 그대로" 가 된다 — 09:00 으로 바꿔도 오늘 몫은 어제 고른
       18:00 에 그대로 간다. 화면은 `매일 09:00 발송` 이라고 말하는데 실제와 다르다
       (원인을 고치고 캐시를 안 지워 그대로였던 `sql_cache` 와 같은 부류다).
    ⚠️ 알림(`kind <> 'briefing'`)은 건드리지 않는다 — 시각을 갖지 않는다.
    ⚠️ `TIMESTAMP(for_date, %s)` 라 그 날짜의 KST 벽시계다. `NOW()` 를 섞지 않는다.
    """

    when = normalize_send_at(send_at)
    return int(
        execute(
            "UPDATE briefing_jandi_outbox SET send_after = TIMESTAMP(for_date, %s) "
            "WHERE user_id = %s AND status = 'pending' AND kind = 'briefing'",
            (when, int(user_id)),
        )
        or 0
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
        "SELECT w.user_id,w.webhook_url,w.send_at,w.muted_sections "
        "FROM user_jandi_webhooks w "
        "JOIN users u ON u.id = w.user_id "
        "WHERE w.enabled = 1 AND u.is_active = 1",
    )


# ── 발송 대기열 ──────────────────────────────────────────────────────────────

def enqueue(user_id: int, for_date: date, webhook_url: str, body: str,
            kind: str = "briefing", dedup_key: str = "",
            title: str = "", link: str = "",
            send_after: datetime | None = None) -> bool:
    """한 건 넣는다. **같은 `dedup_key` 는 두 번 들어가지 않는다.**

    ⛔ 같은 알림을 두 번 보내면 그 다음부터 아무도 안 읽는다 — 브리핑이 '하루 한 건'
       이던 이유와 같다. 브리핑의 키는 날짜이고, 알림의 키는 그 항목 자신이다.

    `send_after` 는 **브리핑에만** 붙는다 (사용자가 고른 도착 시각). 셀라 알림은
    None 이라 지금처럼 다음 회차에 그대로 나간다 — 알림은 "지금 봐 달라" 는
    성격이라 미루면 뜻이 없어진다.
    """

    if not body.strip() or not is_valid_webhook(webhook_url):
        return False
    key = (dedup_key or f"{kind}:{for_date}")[:120]
    changed = execute(
        "INSERT INTO briefing_jandi_outbox "
        "(user_id,for_date,kind,dedup_key,title,link,webhook_url,body,send_after) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) "
        "ON DUPLICATE KEY UPDATE "
        "webhook_url=VALUES(webhook_url),"
        # 이미 보낸 건은 본문을 바꾸지 않는다 (다시 보내지 않으므로 의미도 없다).
        "body=IF(status='pending',VALUES(body),body),"
        "title=IF(status='pending',VALUES(title),title),"
        "link=IF(status='pending',VALUES(link),link),"
        "send_after=IF(status='pending',VALUES(send_after),send_after)",
        (int(user_id), for_date, kind[:24], key, title[:200], link[:300],
         webhook_url, body, send_after),
    )
    return bool(changed)


def pending(limit: int = 50) -> list[dict[str, Any]]:
    """릴레이가 꺼내 갈 것. **도착 시각이 된 것만** 낸다.

    ⛔ 기준 시각은 파이썬이 KST 로 만들어 넘긴다 — `NOW()` 를 쓰면 DB 세션
       타임존이 UTC 일 때 9시간 어긋나고, 그때 나는 것은 에러가 아니라
       **브리핑이 조용히 안 가는 것**이다.
    ⚠️ `send_after IS NULL` 을 빼지 마라 — 셀라 알림이 통째로 멈춘다.
    """

    return fetch_all(
        "SELECT id,user_id,for_date,kind,title,link,webhook_url,body,attempts "
        "FROM briefing_jandi_outbox "
        "WHERE status = 'pending' AND attempts < %s "
        "  AND (send_after IS NULL OR send_after <= %s) "
        "ORDER BY id LIMIT %s",
        (MAX_ATTEMPTS, now_kst(), int(limit)),
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
    # ⛔ 릴레이(`scripts/jandi_briefing_relay.py`)에 같은 목록이 있다 —
    #    한쪽만 고치면 색·제목이 어긋난다 (회귀가 대조한다)
    "group_assign": ("새 사용자가 그룹 배정을 기다립니다", "#e8543f"),
}


#: 잔디 커넥트 본문 상한. ⚠️ 우리가 정한 안전선이다 — 넘으면 **밝히고** 자른다
_JANDI_BODY_LIMIT = 9000


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
    # ⛔ **조용히 자르지 않는다.** 잘린 줄 모르면 "왜 일부만 오지" 가 된다
    #    (2026-09-07 제보의 원인이 정확히 그것이었다). 자를 땐 사실을 적는다.
    text = str(body or "")
    if len(text) > _JANDI_BODY_LIMIT:
        cut = f"\n\n… (본문이 길어 여기까지만 보냅니다 · 전체 {len(text):,}자 — 위 링크에서 전문 확인)"
        text = text[:_JANDI_BODY_LIMIT - len(cut)].rstrip() + cut
    return {"body": text, "connectColor": color, "connectInfo": info}
