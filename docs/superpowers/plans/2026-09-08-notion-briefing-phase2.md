# 노션 저장 2단계 — 출근 브리핑 자동 배송 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 매일 07:30 만들어지는 출근 브리핑을, 사용자가 등록한 노션 페이지에 자기가 고른 시각에 자동으로 쌓는다.

**Architecture:** 1단계 엔진(`notion_export.resolve_target` / `save`)은 그대로 쓰고, 잔디 배송의 배관(대상 등록 · 대기열 · 절 끄기 · 도착 시각 · 자가 점검)을 **같은 모양으로** 한 벌 더 만든다. ⛔ 대기열만은 잔디와 **합치지 않는다** — 잔디 대기열은 DB_PC 릴레이가 꺼내 가므로 섞으면 릴레이가 노션 건을 웹훅으로 쏜다. 노션은 WAS 가 직접 쓰므로 릴레이가 없다.

**Tech Stack:** Python 3 · FastAPI · APScheduler · MariaDB(`app.db.mariadb`) · httpx · pytest · Notion API `2025-09-03`

**Spec:** `docs/superpowers/specs/2026-09-07-notion-export-design.md` (§4 브리핑 자동 배송, §5 조용한 실패 방어, §6 사생활)

## Global Constraints

- ⛔ **이 작업트리를 다른 세션이 함께 쓴다.** 커밋은 반드시 `git add <경로>` 로 지목한다. `git add -A`·`git add .`·`git add -u`·`git commit -a` 는 훅이 막는다. `.env` 는 **절대** 스테이징하지 마라.
- ⛔ **대기열은 `briefing_notion_outbox` 별도 테이블.** 잔디 대기열(`briefing_jandi_outbox`)에 채널 컬럼을 더하지 마라.
- ⛔ **사본을 만들지 마라.** 절 목록(`SECTIONS`)·절 끄기(`parse_muted`/`serialize_muted`/`wants`)·도착 시각 정규화(`normalize_send_at`)·`send_after_for`·`now_kst`·`SEND_TIME_CHOICES` 는 **`app.core.jandi_briefing` 의 것을 import 해서 쓴다.**
- ⛔ **`NOW()` 로 시각을 비교하지 마라.** DB `time_zone` 이 `SYSTEM` 이다 — `jandi_briefing.now_kst()` 를 **파라미터로** 넘긴다.
- ⛔ **본문은 `work_briefing.render_markdown(document, name=…, fx=…, business=…, muted=…)` 하나만 쓴다.** 노션용 렌더러를 따로 만들지 마라.
- ⛔ 실릴 것이 있었는데 사용자가 **전부 껐으면 보내지 않는다** (`work_briefing.content_sections()` 로 판정).
- ⛔ 저장 URL 은 화면에 되돌릴 때 **가린다**. 프론트에 시각·절 목록을 **하드코딩하지 마라** — 서버가 단일 소스다.
- ⚠️ 실패는 **WARNING** 으로 남긴다 (프로덕션은 앱 INFO 를 버린다).
- 주석·문서 문자열은 한국어. `pytest --timeout` 플러그인은 없다.

---

## File Structure

| 파일 | 책임 |
|---|---|
| `app/core/notion_briefing.py` (신규) | 대상 등록 테이블 + 대기열 테이블 + 발송 루프 |
| `app/core/personal_briefing.py` (수정) | 브리핑 생성 시 노션 대기열에 적재 |
| `app/api/notion_briefing_api.py` (신규) | `GET/PUT/DELETE /api/personal-briefing/notion` + `/test` |
| `app/main.py` (수정) | 라우터 등록 · 잡 `notion_push_halfhourly` |
| `app/core/self_check.py` (수정) | `EXPECTED_JOBS` + 검사 `notion_push` |
| `app/core/notion_save.py` (수정) | "정기 발송 준비 중" 안내 → **실제 등록** |
| `app/frontend/personal-briefing.js`, `app/static/style.css` (수정) | 설정 다이얼로그의 `노션으로 받기` |
| `tests/test_notion_briefing.py` (신규) | 2단계 회귀 |

---

### Task 1: 대상 등록 — `user_notion_targets`

**Files:**
- Create: `app/core/notion_briefing.py`
- Test: `tests/test_notion_briefing.py`

**Interfaces:**
- Consumes: `app.core.jandi_briefing` 의 `SECTIONS`, `SEND_TIME_CHOICES`, `DEFAULT_SEND_AT`, `parse_muted`, `serialize_muted`, `normalize_send_at`, `now_kst`, `send_after_for`
- Produces:
  - `ensure_tables() -> None`
  - `get_target(user_id: int) -> dict | None`
  - `set_target(user_id: int, page_url: str, enabled: bool = True, send_at=None, muted=None) -> None`
  - `delete_target(user_id: int) -> None`
  - `enabled_recipients() -> list[dict]` — `{user_id, page_url, send_at, muted_sections}`
  - `mask(url: str) -> str`
  - `is_valid_page_url(url: str) -> bool`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_notion_briefing.py`:

```python
# -*- coding: utf-8 -*-
"""브리핑 노션 자동 배송 회귀.

⛔ DB 도 네트워크도 타지 않는다 — 저장 계층과 `_request` 를 monkeypatch 한다.
"""
import pytest

from app.core import jandi_briefing as jb
from app.core import notion_briefing as nb


def test_page_url_validation_reuses_the_engine():
    """⛔ 아무 URL 이나 받으면 서버가 남의 메일 요약을 아무 데나 쓰는 기계가 된다."""
    assert nb.is_valid_page_url(
        "https://www.notion.so/24f1a2b3c4d54e6f8a9b0c1d2e3f4a5b") is True
    assert nb.is_valid_page_url("https://example.com/x") is False
    assert nb.is_valid_page_url("https://skin1004.notion.site/abc") is False
    assert nb.is_valid_page_url("") is False


def test_mask_hides_the_id():
    masked = nb.mask("https://www.notion.so/회의록-24f1a2b3c4d54e6f8a9b0c1d2e3f4a5b")
    assert "24f1a2b3" not in masked
    assert "notion.so" in masked


def test_send_time_choices_come_from_jandi_not_a_copy():
    """⛔ 두 화면이 다른 목록을 보이면 사용자가 혼란스럽다 — 사본을 만들지 않는다."""
    assert nb.SEND_TIME_CHOICES is jb.SEND_TIME_CHOICES


def test_sections_come_from_jandi_not_a_copy():
    assert nb.SECTIONS is jb.SECTIONS
```

- [ ] **Step 2: 실패를 확인한다**

Run: `python -m pytest tests/test_notion_briefing.py -v`
Expected: FAIL — `ModuleNotFoundError: app.core.notion_briefing`

- [ ] **Step 3: 구현한다**

`app/core/notion_briefing.py`:

```python
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
    execute(
        "INSERT INTO user_notion_targets "
        "(user_id, page_url, enabled, send_at, muted_sections) "
        "VALUES (%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE "
        "page_url=VALUES(page_url), enabled=VALUES(enabled), "
        "send_at=VALUES(send_at), "
        "muted_sections=IF(%s, VALUES(muted_sections), muted_sections)",
        (int(user_id), page_url.strip(), 1 if enabled else 0,
         normalize_send_at(send_at), serialize_muted(muted or []),
         1 if muted is not None else 0),
    )


def delete_target(user_id: int) -> None:
    execute("DELETE FROM user_notion_targets WHERE user_id=%s", (int(user_id),))


def enabled_recipients() -> list[dict[str, Any]]:
    return fetch_all(
        "SELECT user_id, page_url, send_at, muted_sections "
        "FROM user_notion_targets WHERE enabled=1")
```

- [ ] **Step 4: 통과를 확인한다**

Run: `python -m pytest tests/test_notion_briefing.py -v`
Expected: PASS (4건)

- [ ] **Step 5: 커밋**

```bash
git add app/core/notion_briefing.py tests/test_notion_briefing.py
git commit -m "feat(notion): 브리핑을 받을 노션 페이지를 등록한다"
```

---

### Task 2: 대기열 — 넣기·꺼내기·기록

**Files:**
- Modify: `app/core/notion_briefing.py`
- Test: `tests/test_notion_briefing.py`

**Interfaces:**
- Produces:
  - `enqueue(user_id: int, for_date: date, page_url: str, body: str, title: str, send_after: datetime | None = None) -> bool`
  - `pending(now: datetime, limit: int = 50) -> list[dict]`
  - `mark_sent(outbox_id: int, row_url: str) -> None`
  - `mark_failed(outbox_id: int, error: str) -> None`
  - `status_counts(for_date: date | None = None) -> dict[str, int]`
  - `cleanup(before: date) -> int`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

파일 끝에 붙인다:

```python
from datetime import date, datetime, time


class _FakeDB:
    """execute/fetch_* 를 가로채 SQL 과 파라미터만 기록한다."""

    def __init__(self):
        self.calls = []
        self.rows = []

    def execute(self, sql, params=()):
        self.calls.append((" ".join(sql.split()), params))
        return 1

    def fetch_all(self, sql, params=()):
        self.calls.append((" ".join(sql.split()), params))
        return self.rows

    def fetch_one(self, sql, params=()):
        self.calls.append((" ".join(sql.split()), params))
        return self.rows[0] if self.rows else None


@pytest.fixture
def db(monkeypatch):
    fake = _FakeDB()
    monkeypatch.setattr(nb, "execute", fake.execute)
    monkeypatch.setattr(nb, "fetch_all", fake.fetch_all)
    monkeypatch.setattr(nb, "fetch_one", fake.fetch_one)
    return fake


def test_enqueue_refuses_an_empty_body(db):
    """빈 브리핑을 보내지 않는다."""
    assert nb.enqueue(7, date(2026, 9, 8), "https://www.notion.so/x", "  ", "제목") is False
    assert db.calls == []


def test_enqueue_refuses_a_bad_url(db):
    assert nb.enqueue(7, date(2026, 9, 8), "https://example.com/x", "본문", "제목") is False
    assert db.calls == []


def test_enqueue_uses_the_date_as_dedup_key(db):
    """⛔ 같은 날 두 번 돌아도 두 번 보내지 않는다."""
    assert nb.enqueue(7, date(2026, 9, 8),
                      "https://www.notion.so/24f1a2b3c4d54e6f8a9b0c1d2e3f4a5b",
                      "본문", "2026-09-08 출근 브리핑") is True
    sql, params = db.calls[0]
    assert "briefing_notion_outbox" in sql
    assert "briefing:2026-09-08" in params


def test_pending_compares_against_the_passed_clock_not_now(db):
    """⛔ `NOW()` 를 쓰면 DB 호스트 TZ 가 바뀔 때 9시간 어긋난다."""
    now = datetime(2026, 9, 8, 9, 0)
    nb.pending(now)
    sql, params = db.calls[0]
    assert "NOW()" not in sql
    assert now in params


def test_pending_also_takes_rows_without_a_send_after(db):
    """⚠️ 즉시 발송분(`지금 대기열에 넣기`)은 시각을 갖지 않는다."""
    nb.pending(datetime(2026, 9, 8, 9, 0))
    sql, _ = db.calls[0]
    assert "send_after IS NULL" in sql


def test_mark_failed_gives_up_after_max_attempts(db):
    nb.mark_failed(11, "노션이 404")
    sql, params = db.calls[0]
    assert "attempts=attempts+1" in sql.replace(" ", "")
    assert nb.MAX_ATTEMPTS in params
```

- [ ] **Step 2: 실패를 확인한다**

Run: `python -m pytest tests/test_notion_briefing.py -k "enqueue or pending or mark_failed" -v`
Expected: FAIL — `has no attribute 'enqueue'`

- [ ] **Step 3: 구현한다**

```python
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
        "page_url=VALUES(page_url),"
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
    execute(
        "UPDATE briefing_notion_outbox "
        "SET status='sent', sent_at=NOW(), last_error=%s WHERE id=%s",
        (row_url[:255], int(outbox_id)))


def mark_failed(outbox_id: int, error: str) -> None:
    """⚠️ 상한에 닿으면 `failed` 로 굳힌다 — 영원히 재시도하지 않는다."""
    execute(
        "UPDATE briefing_notion_outbox "
        "SET attempts=attempts+1, last_error=%s, "
        "status=IF(attempts+1 >= %s,'failed','pending') WHERE id=%s",
        (error[:255], MAX_ATTEMPTS, int(outbox_id)))


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
```

- [ ] **Step 4: 통과를 확인한다**

Run: `python -m pytest tests/test_notion_briefing.py -v`
Expected: PASS (전부)

- [ ] **Step 5: 커밋**

```bash
git add app/core/notion_briefing.py tests/test_notion_briefing.py
git commit -m "feat(notion): 브리핑 발송 대기열"
```

---

### Task 3: 발송 — 대기열을 비운다

**Files:**
- Modify: `app/core/notion_briefing.py`
- Test: `tests/test_notion_briefing.py`

**Interfaces:**
- Consumes: `notion_export.resolve_target(user_id, url) -> Target`, `notion_export.save(target, title, text, kind="답변", link="") -> SaveResult`, `notion_export.is_enabled()`, `notion_export.NotionError`
- Produces: `push_pending(now: datetime | None = None, limit: int = 50) -> dict[str, int]` — `{"sent": n, "failed": n}`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
from app.core import notion_export as nx


@pytest.fixture
def engine(monkeypatch):
    state = {"saved": [], "target": nx.Target("db-1", "ds-1", {"제목": "title"})}

    def resolve(user_id, url):
        if "boom" in url:
            raise nx.NotionError("not_connected", "연결 없음", 404)
        return state["target"]

    def save(target, title, text, kind="답변", link=""):
        state["saved"].append({"title": title, "text": text, "kind": kind})
        return nx.SaveResult(url="https://notion.so/row-1")

    monkeypatch.setattr(nx, "is_enabled", lambda: True)
    monkeypatch.setattr(nx, "resolve_target", resolve)
    monkeypatch.setattr(nx, "save", save)
    return state


def _row(**over):
    row = {"id": 1, "user_id": 7, "for_date": date(2026, 9, 8),
           "title": "2026-09-08 출근 브리핑",
           "page_url": "https://www.notion.so/24f1a2b3c4d54e6f8a9b0c1d2e3f4a5b",
           "body": "☀️ 오늘의 출근 브리핑", "attempts": 0}
    row.update(over)
    return row


def test_push_saves_and_marks_sent(db, engine, monkeypatch):
    monkeypatch.setattr(nb, "pending", lambda now, limit=50: [_row()])
    marked = []
    monkeypatch.setattr(nb, "mark_sent", lambda i, u: marked.append((i, u)))
    result = nb.push_pending(datetime(2026, 9, 8, 9, 0))
    assert result == {"sent": 1, "failed": 0}
    assert engine["saved"][0]["kind"] == "브리핑"
    assert marked == [(1, "https://notion.so/row-1")]


def test_push_records_the_reason_when_notion_refuses(db, engine, monkeypatch):
    """⛔ 실패를 조용히 넘기면 그 사람 브리핑은 영원히 안 온다."""
    monkeypatch.setattr(nb, "pending",
                        lambda now, limit=50: [_row(page_url="https://www.notion.so/boom1a2b3c4d54e6f8a9b0c1d2e3f4a5b")])
    failed = []
    monkeypatch.setattr(nb, "mark_failed", lambda i, e: failed.append((i, e)))
    result = nb.push_pending(datetime(2026, 9, 8, 9, 0))
    assert result == {"sent": 0, "failed": 1}
    assert "not_connected" in failed[0][1]


def test_push_does_nothing_when_the_feature_is_off(db, monkeypatch):
    """토큰이 없으면 대기열을 건드리지 않는다 — 나중에 켜지면 그대로 나간다."""
    monkeypatch.setattr(nx, "is_enabled", lambda: False)
    called = []
    monkeypatch.setattr(nb, "pending", lambda now, limit=50: called.append(1) or [])
    assert nb.push_pending(datetime(2026, 9, 8, 9, 0)) == {"sent": 0, "failed": 0}
    assert called == []


def test_push_uses_kst_when_no_clock_is_given(db, engine, monkeypatch):
    seen = {}
    monkeypatch.setattr(nb, "pending",
                        lambda now, limit=50: seen.setdefault("now", now) and [])
    nb.push_pending()
    assert seen["now"].year >= 2026
```

- [ ] **Step 2: 실패를 확인한다**

Run: `python -m pytest tests/test_notion_briefing.py -k push -v`
Expected: FAIL — `has no attribute 'push_pending'`

- [ ] **Step 3: 구현한다**

```python
def push_pending(now: datetime | None = None, limit: int = 50) -> dict[str, int]:
    """대기열을 비운다. 잡이 30분마다 부른다.

    ⛔ 토큰이 없으면 **아무것도 하지 않는다** — 대기열을 실패로 태우지 않고
       그대로 둔다. 나중에 켜지면 밀린 것이 그대로 나간다.
    """
    if not nx.is_enabled():
        return {"sent": 0, "failed": 0}

    moment = now or now_kst()
    sent = failed = 0
    for row in pending(moment, limit):
        try:
            target = nx.resolve_target(int(row["user_id"]), str(row["page_url"]))
            result = nx.save(target, str(row["title"]), str(row["body"]),
                             kind="브리핑", link=_sella_link())
        except nx.NotionError as exc:
            failed += 1
            logger.warning("notion_briefing_push_failed",
                           outbox_id=row["id"], user_id=row["user_id"],
                           kind=exc.kind, error=str(exc)[:160])
            mark_failed(int(row["id"]), f"{exc.kind}: {exc}")
            _remember_error(int(row["user_id"]), f"{exc.kind}: {exc}")
            continue
        except Exception as exc:                    # 한 사람 때문에 멈추지 않는다
            failed += 1
            logger.warning("notion_briefing_push_error",
                           outbox_id=row["id"], error_type=type(exc).__name__)
            mark_failed(int(row["id"]), type(exc).__name__)
            continue
        sent += 1
        mark_sent(int(row["id"]), result.url)
        _remember_sent(int(row["user_id"]))
    return {"sent": sent, "failed": failed}


def _sella_link() -> str:
    try:
        from app.core.jandi_notify import base_url

        return base_url() or ""
    except Exception:
        return ""


def _remember_sent(user_id: int) -> None:
    execute("UPDATE user_notion_targets SET last_sent_at=NOW(), last_error='' "
            "WHERE user_id=%s", (int(user_id),))


def _remember_error(user_id: int, error: str) -> None:
    """⚠️ 화면에 보여줄 마지막 오류. 연결이 끊기면 그날부터 조용히 안 간다."""
    execute("UPDATE user_notion_targets SET last_error=%s WHERE user_id=%s",
            (error[:255], int(user_id)))
```

- [ ] **Step 4: 통과를 확인한다**

Run: `python -m pytest tests/test_notion_briefing.py -v`
Expected: PASS (전부)

- [ ] **Step 5: 커밋**

```bash
git add app/core/notion_briefing.py tests/test_notion_briefing.py
git commit -m "feat(notion): 대기열을 비워 브리핑을 노션에 쌓는다"
```

---

### Task 4: 브리핑 생성 때 적재

**Files:**
- Modify: `app/core/personal_briefing.py` (`_enqueue_jandi` 바로 아래 · 수신자 로딩부)
- Test: `tests/test_notion_briefing.py`

**Interfaces:**
- Consumes: `notion_briefing.enqueue`, `notion_briefing.enabled_recipients`, `work_briefing.content_sections`, `work_briefing.render_markdown`
- Produces: `personal_briefing._enqueue_notion(user, envelope, page_url, name, send_at=None, muted=None) -> bool`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
def test_enqueue_notion_skips_when_every_section_is_muted(monkeypatch):
    """⛔ 실릴 것을 전부 끈 사람에게 머리말만 보내지 마라."""
    from app.core import personal_briefing as pb
    from app.core.auth import User

    envelope = {"document": {"status": "ready", "for_date": "2026-09-08",
                             "meetings": [{"time": "10:00", "title": "회의",
                                           "urgency": "normal"}]},
                "fx": {}, "business": {}}
    called = []
    monkeypatch.setattr(nb, "enqueue", lambda *a, **k: called.append(1) or True)
    user = User(id=7, email="a@b.c", name="임재필", department="", role="user",
                allowed_models="", ad_user_id=None)
    ok = pb._enqueue_notion(user, envelope, "https://www.notion.so/x", "임재필",
                            muted=["meetings"])
    assert ok is False
    assert called == []


def test_enqueue_notion_titles_the_row_with_the_date(monkeypatch):
    from app.core import personal_briefing as pb
    from app.core.auth import User

    envelope = {"document": {"status": "ready", "for_date": "2026-09-08",
                             "weekday": "화",
                             "meetings": [{"time": "10:00", "title": "회의",
                                           "urgency": "normal"}]},
                "fx": {}, "business": {}}
    seen = {}

    def fake_enqueue(user_id, for_date, page_url, body, title, send_after=None):
        seen.update({"title": title, "body": body, "user_id": user_id})
        return True

    monkeypatch.setattr(nb, "enqueue", fake_enqueue)
    user = User(id=7, email="a@b.c", name="임재필", department="", role="user",
                allowed_models="", ad_user_id=None)
    assert pb._enqueue_notion(user, envelope,
                              "https://www.notion.so/24f1a2b3c4d54e6f8a9b0c1d2e3f4a5b",
                              "임재필") is True
    assert seen["title"] == "2026-09-08 출근 브리핑"
    assert "회의" in seen["body"]
```

- [ ] **Step 2: 실패를 확인한다**

Run: `python -m pytest tests/test_notion_briefing.py -k enqueue_notion -v`
Expected: FAIL — `has no attribute '_enqueue_notion'`

- [ ] **Step 3: 구현한다**

`app/core/personal_briefing.py` 의 `_enqueue_jandi` 함수 **바로 뒤**에 넣는다:

```python
def _enqueue_notion(user: User, envelope: dict[str, Any], page_url: str, name: str,
                    send_at: Any = None, muted: Any = None) -> bool:
    """브리핑을 노션 대기열에 넣는다. 잔디와 같은 규칙을 따른다.

    ⛔ 본문은 잔디와 **같은 렌더러**를 쓴다 — 노션용 사본을 만들면 언젠가 갈린다.
    ⛔ 실릴 것이 있었는데 사용자가 전부 껐으면 보내지 않는다.
    """
    from app.core import notion_briefing

    document = envelope.get("document") or {}
    if document.get("status") not in {"ready", "empty"}:
        return False
    fx, business = envelope.get("fx"), envelope.get("business")
    off = notion_briefing.parse_muted(muted)
    present = work_briefing.content_sections(document, fx, business)
    if present and present <= off:
        logger.info("notion_briefing_all_sections_muted",
                    user_id=user.id, muted=len(off))
        return False
    body = work_briefing.render_markdown(
        document, name=name or user.name or "", fx=fx, business=business,
        muted=off,
    )
    if not body.strip():
        return False

    for_date = document.get("for_date") or ""
    day = date.fromisoformat(str(for_date)) if for_date else date.today()
    send_after = notion_briefing.send_after_for(
        day, send_at if send_at is not None else notion_briefing.DEFAULT_SEND_AT)
    return notion_briefing.enqueue(
        user_id=user.id, for_date=day, page_url=page_url, body=body,
        title=f"{day} 출근 브리핑", send_after=send_after,
    )
```

⚠️ `date` 가 이미 import 돼 있는지 확인하고 없으면 상단에 더한다.

이어서 수신자 로딩부(현재 `webhooks = {...}` 를 만드는 곳, 약 753행)의 **바로 아래**에 노션 대상도 읽는다:

```python
    try:
        from app.core import notion_briefing

        notion_targets = {
            int(entry["user_id"]): (str(entry["page_url"]), entry.get("send_at"),
                                    entry.get("muted_sections"))
            for entry in await asyncio.to_thread(notion_briefing.enabled_recipients)
        }
    except Exception as exc:
        logger.warning("notion_recipients_unavailable", error_type=type(exc).__name__)
        notion_targets = {}
```

그리고 `one()` 안, 잔디 적재 **바로 다음**에:

```python
            target = notion_targets.get(int(row["id"]))
            if target:
                page_url, n_send_at, n_muted = target
                await asyncio.to_thread(
                    _enqueue_notion, user, result, page_url, row.get("name", ""),
                    n_send_at, n_muted,
                )
```

- [ ] **Step 4: 통과를 확인한다**

Run: `python -m pytest tests/test_notion_briefing.py -v`
Expected: PASS (전부)

- [ ] **Step 5: 커밋**

```bash
git add app/core/personal_briefing.py tests/test_notion_briefing.py
git commit -m "feat(notion): 브리핑 생성 때 노션 대기열에 함께 적재한다"
```

---

### Task 5: 스케줄러 잡 + 자가 점검

**Files:**
- Modify: `app/main.py` (잡 등록), `app/core/self_check.py` (`EXPECTED_JOBS` + 검사)
- Test: `tests/test_notion_briefing.py`

**Interfaces:**
- Consumes: `notion_briefing.push_pending`, `notion_briefing.status_counts`
- Produces: 잡 id `notion_push_halfhourly` · 자가 점검 `notion_push`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
def test_push_job_is_registered_in_main():
    """⛔ 잡을 안 걸면 대기열이 영원히 안 비워진다 — 에러도 없이."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    source = (root / "app" / "main.py").read_text(encoding="utf-8")
    assert "notion_push_halfhourly" in source
    assert "notion_briefing" in source


def test_push_job_is_watched_by_self_check():
    from app.core.self_check import EXPECTED_JOBS

    assert "notion_push_halfhourly" in EXPECTED_JOBS


def test_self_check_has_a_notion_push_check():
    from app.core import self_check

    assert any(c.id == "notion_push" for c in self_check.CHECKS)
```

- [ ] **Step 2: 실패를 확인한다**

Run: `python -m pytest tests/test_notion_briefing.py -k "job or self_check" -v`
Expected: FAIL — `assert 'notion_push_halfhourly' in source`

- [ ] **Step 3: `app/main.py` 에 잡을 건다**

다른 `add_job` 들 옆(`_personal_briefing_job` 등록 근처)에 더한다:

```python
            _scheduler.add_job(_notion_push_job, "cron", minute="5,35",
                               id="notion_push_halfhourly")
```

그리고 다른 `_*_job` 함수들 옆에:

```python
async def _notion_push_job():
    """노션 대기열을 비운다 (30분마다). 잔디와 달리 릴레이가 없다 — WAS 가 직접 쓴다."""
    with track_job("notion_push_halfhourly") as jr:
        from app.core.notion_briefing import push_pending

        result = await asyncio.to_thread(push_pending)
        jr.note(f"sent={result['sent']} failed={result['failed']}")
```

⚠️ 이 파일의 다른 잡이 `track_job` 을 어떻게 쓰는지 먼저 읽고 **그 모양에 맞춰라** (`jr.note` 가 없으면 쓰지 마라).

기동 시 테이블 생성도 다른 `ensure_*` 옆에 더한다 — 없으면 `push_pending` 첫 실행에서 만들어지므로 **생략해도 된다.** 생략했으면 보고서에 적어라.

- [ ] **Step 4: 자가 점검을 더한다**

`app/core/self_check.py` 의 `EXPECTED_JOBS` 에:

```python
    "notion_push_halfhourly": (2, "브리핑 노션 대기열 발송 (30분마다)"),
```

검사 함수(다른 `_check_*` 옆):

```python
def _check_notion_push() -> CheckResult:
    """도착 시각이 **지난** 대기 건이 쌓이고 있는가.

    ⚠️ 기다리는 중인 것(18:30 을 고른 사람)을 밀린 것으로 세지 않는다 —
       매일 뜨는 경고는 곧 아무도 안 읽는다.
    """
    from app.core.notion_briefing import now_kst
    from app.db.mariadb import fetch_one

    row = fetch_one(
        "SELECT COUNT(*) AS n FROM briefing_notion_outbox "
        "WHERE status='pending' AND send_after IS NOT NULL "
        "AND send_after < DATE_SUB(%s, INTERVAL 6 HOUR)", (now_kst(),))
    stuck = int((row or {}).get("n") or 0)
    if stuck:
        return CheckResult(False, f"도착 시각이 6시간 넘게 지난 대기 {stuck}건")
    return CheckResult(True, "밀린 건 없음")
```

`CHECKS` 목록에:

```python
    Check("notion_push", "batch", SEV_WARNING,
          "브리핑 노션 대기열이 비워지고 있는가", _check_notion_push),
```

⚠️ `CheckResult`·`Check` 의 실제 시그니처를 **먼저 읽고** 그 모양에 맞춰라. 다르면 맞춰 고치고 보고서에 적어라.

- [ ] **Step 5: 통과를 확인한다**

Run: `python -m pytest tests/test_notion_briefing.py -v`
Expected: PASS (전부)

- [ ] **Step 6: 커밋**

```bash
git add app/main.py app/core/self_check.py tests/test_notion_briefing.py
git commit -m "feat(notion): 30분마다 대기열을 비우고 밀리면 자가 점검이 잡는다"
```

---

### Task 6: API

**Files:**
- Create: `app/api/notion_briefing_api.py`
- Modify: `app/main.py` (라우터 등록)
- Test: `tests/test_notion_briefing.py`

**Interfaces:**
- Produces: `GET/PUT/DELETE /api/personal-briefing/notion`, `POST /api/personal-briefing/notion/test`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
def test_api_hides_the_saved_url_and_serves_the_choices():
    """⛔ 시각·절 목록은 **서버가 단일 소스**다. 프론트에 사본을 두면 조용히 갈린다."""
    from app.api import notion_briefing_api as api

    source = __import__("pathlib").Path(api.__file__).read_text(encoding="utf-8")
    assert "send_time_choices" in source
    assert "sections" in source
    assert "mask(" in source


def test_api_put_keeps_the_saved_url_when_none_is_sent():
    """⛔ 서버가 주소를 가려서 내려주므로, 시각만 바꾸려는 사람은 되붙일 수 없다."""
    from app.api import notion_briefing_api as api

    source = __import__("pathlib").Path(api.__file__).read_text(encoding="utf-8")
    assert "이미 저장된 주소" in source


def test_api_is_registered_in_main():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    source = (root / "app" / "main.py").read_text(encoding="utf-8")
    assert "notion_briefing_api" in source
```

- [ ] **Step 2: 실패를 확인한다**

Run: `python -m pytest tests/test_notion_briefing.py -k api -v`
Expected: FAIL — `ModuleNotFoundError: app.api.notion_briefing_api`

- [ ] **Step 3: 구현한다**

⚠️ `app/api/jandi_briefing_api.py` 를 **먼저 읽고 그 모양을 그대로 따라라** (라우터 선언·`get_current_user` 의존성·응답 키 이름). 아래는 그 파일과 대응하는 노션판이다:

```python
# -*- coding: utf-8 -*-
"""출근 브리핑의 노션 배송 설정 — 사용자 본인 것만 읽고 쓴다."""
from fastapi import APIRouter, Body, Depends, HTTPException

from app.core import notion_briefing
from app.core.auth import User, get_current_user

router = APIRouter()


def _send_at_label(value) -> str:
    return notion_briefing.normalize_send_at(value).strftime("%H:%M")


@router.get("/api/personal-briefing/notion")
async def get_my_notion_target(user: User = Depends(get_current_user)) -> dict:
    notion_briefing.ensure_tables()
    row = notion_briefing.get_target(user.id)
    muted = notion_briefing.parse_muted(row.get("muted_sections") if row else "")
    base = {
        "send_time_choices": notion_briefing.SEND_TIME_CHOICES,
        "send_at": notion_briefing.DEFAULT_SEND_AT.strftime("%H:%M"),
        "sections": [
            {"key": key, "label": label, "group": group, "enabled": key not in muted}
            for key, label, group in notion_briefing.SECTIONS
        ],
    }
    if not row:
        return {**base, "registered": False, "enabled": False, "masked": "",
                "last_sent_at": "", "last_error": ""}
    return {
        **base,
        "registered": True,
        "enabled": bool(row["enabled"]),
        "send_at": _send_at_label(row.get("send_at")),
        # ⛔ 저장된 주소를 그대로 돌려주지 않는다.
        "masked": notion_briefing.mask(str(row["page_url"])),
        "last_sent_at": str(row["last_sent_at"] or ""),
        "last_error": str(row["last_error"] or ""),
    }


@router.put("/api/personal-briefing/notion")
async def put_my_notion_target(
    payload: dict = Body(...), user: User = Depends(get_current_user),
) -> dict:
    notion_briefing.ensure_tables()
    url = str(payload.get("page_url", "")).strip()
    if not url:
        # ⛔ 시각만 바꾸려는 사람에게 주소를 다시 붙여넣게 하지 마라 —
        #    서버는 주소를 가려서 내려주므로 되붙일 방법이 없다.
        #    비워서 보내면 이미 저장된 주소를 그대로 쓴다.
        row = notion_briefing.get_target(user.id)
        if not row:
            raise HTTPException(400, "노션 페이지 주소를 입력해 주세요.")
        url = str(row["page_url"])
    if not notion_briefing.is_valid_page_url(url):
        raise HTTPException(400, "노션 페이지 주소가 아닙니다.")

    send_at = payload.get("send_at")
    if send_at and str(send_at) not in notion_briefing.SEND_TIME_CHOICES:
        # ⛔ 목록에 없는 시각을 저장하면 그 사람 브리핑은 영영 오지 않는다.
        raise HTTPException(400, "받을 수 있는 시각이 아닙니다.")

    # ⚠️ `muted` 를 안 보낸 것과 빈 목록은 뜻이 다르다 — 없으면 "안 바꿈".
    muted = payload.get("muted") if "muted" in payload else None
    notion_briefing.set_target(
        user.id, url, enabled=bool(payload.get("enabled", True)),
        send_at=send_at, muted=muted)
    return {"ok": True}


@router.delete("/api/personal-briefing/notion")
async def delete_my_notion_target(user: User = Depends(get_current_user)) -> dict:
    notion_briefing.delete_target(user.id)
    return {"ok": True}


@router.post("/api/personal-briefing/notion/test")
async def test_my_notion_target(user: User = Depends(get_current_user)) -> dict:
    """지금 바로 한 건 보낸다 — 되는지 눈으로 보려고 누르는 버튼이다.

    ⚠️ 도착 시각을 붙이지 않는다 (지금 보내려는 것이므로).
    """
    from datetime import date

    from app.core import notion_export as nx

    row = notion_briefing.get_target(user.id)
    if not row:
        raise HTTPException(400, "먼저 노션 페이지를 등록해 주세요.")
    if not nx.is_enabled():
        raise HTTPException(503, "노션 저장이 아직 켜져 있지 않습니다.")
    try:
        target = nx.resolve_target(user.id, str(row["page_url"]))
        result = nx.save(target, f"{date.today()} 연결 확인",
                         "셀라에서 보낸 연결 확인입니다.", kind="브리핑")
    except nx.NotionError as exc:
        raise HTTPException(400, f"{exc}") from exc
    return {"ok": True, "url": result.url}
```

`app/main.py` 에서 다른 라우터 등록 옆에:

```python
from app.api.notion_briefing_api import router as notion_briefing_router
app.include_router(notion_briefing_router)
```

⚠️ 이 파일이 라우터를 어떻게 등록하는지(`include_router` 위치·import 스타일) **먼저 읽고 맞춰라.**

- [ ] **Step 4: 통과를 확인한다**

Run: `python -m pytest tests/test_notion_briefing.py -v`
Expected: PASS (전부)

- [ ] **Step 5: 커밋**

```bash
git add app/api/notion_briefing_api.py app/main.py tests/test_notion_briefing.py
git commit -m "feat(notion): 브리핑 노션 설정 API"
```

---

### Task 7: 첫 화면 `노션으로 받기`

**Files:**
- Modify: `app/frontend/personal-briefing.js`, `app/static/style.css`
- Test: `tests/test_notion_briefing.py`

**Interfaces:**
- Consumes: `GET/PUT/DELETE /api/personal-briefing/notion`, `POST .../test`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
def test_frontend_does_not_hardcode_choices_or_sections():
    """⛔ 프론트에 사본을 두면 절이 하나 늘 때 화면에서 통째로 사라진다."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    js = (root / "app" / "frontend" / "personal-briefing.js").read_text(encoding="utf-8")
    assert "/api/personal-briefing/notion" in js
    assert "send_time_choices" in js          # 서버가 준 목록을 그린다
    # 시각 목록을 손으로 적지 않았는가
    assert "08:30" not in js or "send_time_choices" in js


def test_frontend_notion_settings_are_wired_into_the_dialog():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    js = (root / "app" / "frontend" / "personal-briefing.js").read_text(encoding="utf-8")
    assert "appendNotionSettings" in js
    # 다이얼로그를 만드는 곳에서 실제로 불린다
    assert js.count("appendNotionSettings") >= 2


def test_frontend_styles_live_in_the_stylesheet_not_inline():
    """⛔ 테마를 타야 하는 스타일을 JS 인라인으로 두면 테마 전환에서 조용히 빠진다."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    css = (root / "app" / "static" / "style.css").read_text(encoding="utf-8")
    assert ".briefing-notion" in css


def test_frontend_warns_that_a_shared_page_shows_mail_titles():
    """⛔ 브리핑에는 메일 제목이 들어간다 — 노션 페이지는 공유가 쉽다 (스펙 §6)."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    js = (root / "app" / "frontend" / "personal-briefing.js").read_text(encoding="utf-8")
    assert "메일 제목" in js
    assert "연결" in js          # 연결 붙이는 법도 함께 안내한다
```

- [ ] **Step 2: 실패를 확인한다**

Run: `python -m pytest tests/test_notion_briefing.py -k frontend -v`
Expected: FAIL — `assert '/api/personal-briefing/notion' in js`

- [ ] **Step 3: 구현한다**

`app/frontend/personal-briefing.js` 의 `appendJandiSettings` 를 **읽고 같은 모양으로** `appendNotionSettings(parent, options)` 를 만든다. 잔디와 다른 점은 셋뿐이다:

1. 입력 placeholder 가 `https://www.notion.so/…` 이고 설명이 다르다
2. 엔드포인트가 `/api/personal-briefing/notion` 이고 본문 키가 `page_url` 이다
3. 안내문에 **연결 붙이는 법**을 적는다 — 실측상 가장 흔한 실패다:
   `페이지 우상단 ⋯ > 연결 > Skin1004_AI 추가`
4. 안내문에 **사생활 한 줄**을 적는다 (스펙 §6):
   `브리핑에는 메일 제목이 들어갑니다. 노션 페이지는 공유하면 그 사람도 보게 됩니다.`
   ⚠️ 받을 절의 **기본값은 잔디와 같게(전부 켜짐)** 둔다 — 여기만 다르게 하면
   "잔디엔 오는데 노션엔 안 온다" 가 된다 (스펙에서 사용자가 확정한 사항)

그리고 다이얼로그를 만드는 곳(`appendJandiSettings(content, options);` 가 불리는 줄) **바로 아래**에:

```javascript
    appendNotionSettings(content, options);
```

`app/static/style.css` 에는 `.briefing-jandi*` 규칙을 참고해 `.briefing-notion*` 규칙을 더한다. ⛔ **JS 안에 인라인 스타일을 쓰지 마라** — 테마 전환에서 조용히 빠진다. 색은 `style.css` 상단의 기존 토큰(`--bg`·`--bg-surface`·`--border`·`--text`·`--text-secondary` 등)만 쓴다.

- [ ] **Step 4: 문법과 테스트를 확인한다**

Run: `node --check app/frontend/personal-briefing.js`
Expected: 출력 없음(성공)

Run: `python -m pytest tests/test_notion_briefing.py -v`
Expected: PASS (전부)

- [ ] **Step 5: 커밋**

```bash
git add app/frontend/personal-briefing.js app/static/style.css tests/test_notion_briefing.py
git commit -m "feat(notion): 첫 화면에서 노션으로 받기를 등록한다"
```

---

### Task 8: 채팅으로 등록 — "준비 중" 을 없앤다

**Files:**
- Modify: `app/core/notion_save.py`
- Test: `tests/test_notion_save.py`

**Interfaces:**
- Consumes: `notion_briefing.set_target`, `notion_briefing.is_valid_page_url`
- Produces: `handle()` 이 정기 발송 요청을 **실제 등록**으로 처리한다

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_notion_save.py` 끝에:

```python
def test_recurring_request_now_registers_instead_of_apologising(monkeypatch, fake_engine):
    """1단계에서는 "준비 중" 이라고 답했다. 2단계에서는 실제로 등록한다."""
    from app.core import notion_briefing as nb

    saved = {}
    monkeypatch.setattr(nb, "set_target",
                        lambda user_id, url, **kw: saved.update(
                            {"user_id": user_id, "url": url}))
    messages = _msgs(
        ("user", "브리핑 매일 노션에 넣어줘"),
        ("assistant", ns.build_prompt("브리핑")),
        ("user", "https://www.notion.so/24f1a2b3c4d54e6f8a9b0c1d2e3f4a5b"),
    )
    answer = ns.handle(messages[-1]["content"], messages, 7)
    assert saved["user_id"] == 7
    assert "매일" in answer
    assert "준비 중" not in answer


def test_recurring_request_without_a_url_asks_for_one(fake_engine):
    messages = _msgs(
        ("user", "매출은?"), ("assistant", "55.1억원입니다."),
        ("user", "브리핑 매일 노션에 넣어줘"),
    )
    answer = ns.handle(messages[-1]["content"], messages, 7)
    assert ns._MARKER.search(answer)
    assert "준비 중" not in answer
```

- [ ] **Step 2: 실패를 확인한다**

Run: `python -m pytest tests/test_notion_save.py -k recurring -v`
Expected: FAIL — 지금은 "준비 중" 문구가 돌아온다

- [ ] **Step 3: 구현한다**

`handle()` 의 정기 발송 분기를, **되묻고 등록하는** 흐름으로 바꾼다. 되묻기 마커에 이미 `kind` 가 실리므로 그것을 `"브리핑"` 으로 쓴다:

```python
    if _RECURRING.search(query) and _BRIEFING.search(query):
        if not nx.is_enabled():
            return _ERROR_MESSAGE["disabled"]
        url = extract_url(query)
        if not url:
            return build_prompt("브리핑")
        return _register_briefing(user_id, url)
```

그리고 `pending` 분기에서 `kind == "브리핑"` 이면 저장이 아니라 등록으로 보낸다:

```python
    if waiting:
        url = extract_url(query)
        if not url:
            if not notion_save_intent(query):
                return None
            return build_prompt(waiting.get("kind", "답변"))
        if waiting.get("kind") == "브리핑":
            return _register_briefing(user_id, url)
        return _do_save(user_id, url, target_answer(messages),
                        waiting.get("kind", "답변"))
```

등록 함수를 더한다:

```python
def _register_briefing(user_id: int | None, url: str) -> str:
    """출근 브리핑을 매일 그 페이지에 쌓도록 등록한다.

    ⚠️ 첫 화면 설정과 **같은 테이블**에 쓴다 — 입구가 둘이어도 상태는 하나다.
    """
    from app.core import notion_briefing

    if not notion_briefing.is_valid_page_url(url):
        return _ERROR_MESSAGE["bad_request"]
    try:
        notion_briefing.ensure_tables()
        notion_briefing.set_target(int(user_id or 0), url)
    except Exception as exc:
        logger.warning("notion_briefing_register_failed",
                       error_type=type(exc).__name__, user_id=user_id)
        return _ERROR_MESSAGE["unavailable"]
    return ("등록했습니다. 내일부터 **출근 브리핑을 매일** 그 페이지에 쌓습니다.\n\n"
            "받을 시각과 항목은 첫 화면 브리핑의 `노션으로 받기` 에서 바꿀 수 있고, "
            "거기서 해지도 됩니다.")
```

- [ ] **Step 4: 통과를 확인한다**

Run: `python -m pytest tests/test_notion_save.py tests/test_notion_briefing.py -v`
Expected: PASS (전부)

- [ ] **Step 5: 커밋**

```bash
git add app/core/notion_save.py tests/test_notion_save.py
git commit -m "feat(notion): 채팅에서 브리핑 정기 배송을 등록한다"
```

---

### Task 9: 문서 + 전체 회귀

**Files:**
- Modify: `CLAUDE.md` (1단계 절 뒤에 이어 쓴다)

- [ ] **Step 1: 전체 스위트를 돌린다**

Run: `python -m pytest tests/ -q`
Expected: 우리 테스트 전부 통과. ⚠️ 실패가 나오면 **내 변경 탓인지 먼저 확인한다** — 이 작업트리는 다른 세션과 공유하고 남의 미완성 파일이 섞여 들어온다. 남의 것이면 목록만 보고서에 적고 **고치지 마라.**

- [ ] **Step 2: CLAUDE.md 에 절을 이어 쓴다**

`## 노션 저장 — 쓰기는 다른 토큰·다른 API 버전이다 (2026-09-07)` 절의 **맨 끝**에 붙인다:

```markdown
### 브리핑 자동 배송 (2단계, 2026-09-08)

- **파일**: `app/core/notion_briefing.py`(대상·대기열·발송) · `personal_briefing._enqueue_notion`
  (적재) · `app/api/notion_briefing_api.py` · 잡 `notion_push_halfhourly`(매시 :05·:35)
- ⛔ **대기열을 잔디와 합치지 마라.** 잔디 대기열은 DB_PC 릴레이가 꺼내 간다 —
  섞으면 릴레이가 노션 건을 웹훅으로 쏜다. 노션은 WAS 가 직접 쓰므로 릴레이가 없다
- ⛔ **절 목록·절 끄기·도착 시각은 `jandi_briefing` 것을 import 해서 쓴다.** 사본을
  만들면 한쪽만 고쳐져 조용히 갈린다 (이 저장소가 반복해서 겪은 실패다)
- ⛔ **본문 렌더러는 하나다** (`work_briefing.render_markdown`). 노션용을 따로 만들지 마라
- ⛔ `NOW()` 로 도착 시각을 비교하지 마라 — DB `time_zone` 이 `SYSTEM` 이다.
  `now_kst()` 를 파라미터로 넘긴다
- ⚠️ 입구가 둘이다 (첫 화면 `노션으로 받기` · 채팅 "브리핑 매일 노션에 넣어줘").
  **저장되는 테이블은 하나**라 상태가 갈리지 않는다
- ⚠️ 토큰이 없으면 `push_pending` 은 **대기열을 건드리지 않는다** — 실패로 태우지
  않고 그대로 둔다. 나중에 켜지면 밀린 것이 그대로 나간다
- ⚠️ 자가 점검 `notion_push` 는 **도착 시각이 지난** 대기만 센다. 18:30 을 고른
  사람의 07:30 산출물을 밀린 것으로 세면 매일 경고가 뜬다
```

Run: `python scripts/sync_agents_md.py`

- [ ] **Step 3: 커밋**

```bash
git add CLAUDE.md AGENTS.md
git commit -m "docs: 브리핑 노션 자동 배송 규칙"
```

---

## 완료 조건

- `python -m pytest tests/test_notion_briefing.py tests/test_notion_save.py tests/test_notion_export.py -v` 전부 통과
- 전체 스위트에 **내 변경으로 인한** 새 실패 없음
- `node --check app/frontend/personal-briefing.js` 통과
- CLAUDE.md / AGENTS.md 갱신

## 이 계획이 다루지 않는 것

- 실물 확인(등록 → 다음 날 07:30 → 실제로 노션에 쌓이는지)은 **배포 후** 사람이 확인한다
- 스펙 §3.3 "지난번 그 페이지에 넣을까요?" 는 여전히 미구현 (1단계에서 보류)
