# Login Personal Briefing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 로그인 직후 새 대화 화면에 오늘 받은 메일, 오늘 포함 7일 일정, 기존 업무 지표, 검증된 우선 확인 3건을 저장본 우선 방식으로 표시한다.

**Architecture:** JWT 사용자를 유일한 소유자 키로 삼는 개인 브리핑 집계기가 Gmail/Calendar 경량 조회를 병렬 실행하고 MariaDB 사용자별 단일 스냅샷에 정규화 결과만 저장한다. GET은 저장본과 기존 `daily_briefings`를 즉시 결합하고, POST refresh와 08:30 선계산이 같은 집계 함수를 호출한다. 프론트는 웰컴 화면 카드만 갱신하며 기존 대화 화면은 건드리지 않는다.

**Tech Stack:** Python 3.11, FastAPI, PyJWT, MariaDB/PyMySQL, Google Gmail/Calendar APIs, Gemini Flash JSON mode, vanilla JavaScript, CSS, pytest, Playwright.

**Spec:** `docs/superpowers/specs/2026-08-25-login-personal-briefing-design.md`

## Global Constraints

- Google 권한은 기존 `gmail.readonly`, `calendar.readonly`, `drive.readonly`보다 넓히지 않는다.
- 첫 화면 경로에서는 Gmail 본문과 첨부파일을 내려받지 않는다. 최근 20건의 헤더와 `snippet`만 요약 입력에 사용한다.
- 날짜 경계와 표시는 `Asia/Seoul`; 일정 범위는 오늘 00:00 이상 오늘+7일 00:00 미만이다.
- 개인 정보 소유자는 JWT의 `user.id`·`user.email`로만 정한다. 요청의 `user_id`·`user_email`은 받지 않는다.
- 스냅샷은 사용자당 한 행이며 메일 `snippet`·본문·첨부파일·OAuth 토큰은 DB, API 응답, 로그에 저장하지 않는다.
- LLM이 만든 메일 후보는 실제 입력 `message_id`가 존재할 때만 노출한다. 건수·시간·제목·보낸 사람은 원본 API 값만 사용한다.
- 캐시 TTL은 10분, Calendar/Gmail 섹션 타임아웃은 각 10초, LLM 타임아웃은 8초, refresh 전체 상한은 15초다.
- 기존 `briefing_opt_out` 사용자는 업무 지표 내용을 자동 노출하지 않는다.
- 외부 링크 허용 대상은 `mail.google.com`, `calendar.google.com`, `www.google.com/calendar/`뿐이다.
- 새 패키지를 추가하지 않는다. OAuth state 서명은 이미 설치된 PyJWT와 기존 `JWT_SECRET_KEY`를 사용한다.
- 실제 프로덕션은 `10.1.100.5 → 10.1.150.5`; 배포는 `./sshenv/Scripts/python scripts/deploy_new_server.py was`다.

---

## File Structure

### 새 파일

- `app/core/google_oauth_state.py` — OAuth state 발급·서명 검증·일회성 nonce 소비와 DDL.
- `app/core/personal_briefing_store.py` — 사용자별 단일 스냅샷 저장·조회·삭제·만료 정리.
- `app/core/personal_briefing.py` — KST 범위, GWS 수집, 메일 JSON 요약 검증, 우선 확인 조합, 아침 선계산.
- `app/api/personal_briefing_api.py` — JWT 전용 GET/POST API와 기능 플래그 경계.
- `app/frontend/personal-briefing.js` — 웰컴 카드 렌더러와 저장본→refresh 상태 전환.
- `tests/test_google_auth_routes.py` — OAuth 소유권·state·revoke 보안 회귀.
- `tests/test_gws_digest.py` — 본문을 받지 않는 Gmail 메타데이터와 고정 Calendar 구간.
- `tests/test_personal_briefing_store.py` — 소유자 WHERE, JSON 직렬화, 날짜·계정 격리.
- `tests/test_personal_briefing.py` — 요약 검증, 우선순위, 부분 실패, TTL, 선계산.
- `tests/test_personal_briefing_api.py` — 인증·기능 플래그·사용자 입력면 부재.
- `tests/frontend/test_personal_briefing_welcome.py` — 카드 상태, 대화 비침범, 모바일, 접근성.

### 수정 파일

- `app/core/google_auth.py` — `get_auth_url()`이 호출자가 만든 서명 state를 받도록 계약 변경.
- `app/core/google_workspace.py` — 경량 Gmail digest와 고정 구간 Calendar 조회 추가.
- `app/agents/gws_agent.py` — 미연결 답변이 사용자 이메일을 담은 직접 OAuth URL을 만들지 않게 변경.
- `app/api/auth_routes.py` — login/status/revoke/callback을 JWT 사용자와 서명 state에 결속.
- `app/config.py` — `personal_briefing_enabled: bool = True` 기능 플래그.
- `app/main.py` — 신규 테이블 초기화, API 라우터, 08:30 잡 등록.
- `app/core/self_check.py` — `personal_briefing_daily`을 `EXPECTED_JOBS`에 등록.
- `app/frontend/chat.html` — 웰컴 브리핑 컨테이너와 스크립트 로드, 정적 버전 증가.
- `app/frontend/chat.js` — 안전한 OAuth 엔드포인트, 브리핑 컨트롤러 초기화·홈 복귀 연결.
- `app/static/style.css` — 데스크톱 2열/모바일 1열 카드, 상태, skeleton, 말줄임·focus 스타일.
- `tests/test_gws_gmail.py` — 미연결 인증 마커의 상대 URL 회귀.

---

### Task 1: Google OAuth 소유권과 state 보안 경계

**Files:**
- Create: `app/core/google_oauth_state.py`
- Create: `tests/test_google_auth_routes.py`
- Modify: `app/core/google_auth.py:248-272`
- Modify: `app/api/auth_routes.py:1-174`
- Modify: `app/agents/gws_agent.py:275-297`
- Modify: `app/frontend/chat.js:1987-1991,3958-4023`
- Modify: `tests/test_gws_gmail.py`

**Interfaces:**
- Produces: `issue_state(user_id: int, user_email: str, now: datetime | None = None) -> str`
- Produces: `consume_state(token: str, current_user_id: int, now: datetime | None = None) -> dict[str, object]`
- Produces: `ensure_oauth_state_table() -> None`
- Changes: `GoogleAuthManager.get_auth_url(user_email: str, *, state: str, redirect_uri: str = "") -> str`
- HTTP: `/auth/google/login|status|revoke` no longer accept or trust `user_email`; callback requires the active JWT user.

- [ ] **Step 1: Write failing unit tests for signed, expiring, single-use state**

```python
# tests/test_google_auth_routes.py
from datetime import datetime, timedelta, timezone
import pytest

from app.core import google_oauth_state

UTC = timezone.utc


def test_oauth_state_is_tied_to_current_user_and_single_use(monkeypatch):
    now = datetime(2026, 8, 25, 0, 0, tzinfo=UTC)
    writes = []
    monkeypatch.setattr(google_oauth_state, "execute", lambda sql, p=(): writes.append((sql, p)) or 1)
    monkeypatch.setattr(
        google_oauth_state,
        "get_settings",
        lambda: type("S", (), {"jwt_secret_key": "s" * 64})(),
    )

    token = google_oauth_state.issue_state(7, "owner@example.com", now=now)
    payload = google_oauth_state.consume_state(token, 7, now=now + timedelta(minutes=1))

    assert payload["user_id"] == 7
    assert payload["email"] == "owner@example.com"
    assert any("used_at IS NULL" in sql and params[1] == 7 for sql, params in writes)


def test_oauth_state_rejects_other_user(monkeypatch):
    monkeypatch.setattr(google_oauth_state, "execute", lambda *_a, **_k: 1)
    monkeypatch.setattr(
        google_oauth_state,
        "get_settings",
        lambda: type("S", (), {"jwt_secret_key": "s" * 64})(),
    )
    token = google_oauth_state.issue_state(7, "owner@example.com")
    with pytest.raises(ValueError, match="user"):
        google_oauth_state.consume_state(token, 8)
```

- [ ] **Step 2: Run the state tests and verify RED**

Run: `pytest tests/test_google_auth_routes.py -q`

Expected: collection fails with `ImportError: cannot import name 'google_oauth_state'`.

- [ ] **Step 3: Implement the OAuth state store with atomic nonce consumption**

```python
# app/core/google_oauth_state.py
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

import jwt

from app.config import get_settings, validate_jwt_secret
from app.db.mariadb import execute

PURPOSE = "gws_oauth"
TTL = timedelta(minutes=10)

_DDL = """
CREATE TABLE IF NOT EXISTS google_oauth_states (
    nonce_hash CHAR(64) PRIMARY KEY,
    user_id INT NOT NULL,
    user_email VARCHAR(320) NOT NULL,
    expires_at DATETIME NOT NULL,
    used_at DATETIME NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_oauth_state_expiry (expires_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""


def ensure_oauth_state_table() -> None:
    execute(_DDL)
    execute("DELETE FROM google_oauth_states WHERE expires_at < DATE_SUB(NOW(), INTERVAL 1 DAY)")


def issue_state(user_id: int, user_email: str, now: datetime | None = None) -> str:
    current = now or datetime.now(timezone.utc)
    nonce = secrets.token_urlsafe(32)
    nonce_hash = hashlib.sha256(nonce.encode()).hexdigest()
    expires = current + TTL
    execute(
        "INSERT INTO google_oauth_states (nonce_hash,user_id,user_email,expires_at) VALUES (%s,%s,%s,%s)",
        (nonce_hash, int(user_id), user_email, expires.replace(tzinfo=None)),
    )
    payload = {
        "purpose": PURPOSE, "user_id": int(user_id), "email": user_email,
        "nonce": nonce, "iat": current, "exp": expires,
    }
    secret = validate_jwt_secret(get_settings().jwt_secret_key)
    return jwt.encode(payload, secret, algorithm="HS256")


def consume_state(token: str, current_user_id: int, now: datetime | None = None) -> dict[str, object]:
    secret = validate_jwt_secret(get_settings().jwt_secret_key)
    payload = jwt.decode(token, secret, algorithms=["HS256"], options={"verify_exp": now is None})
    if now is not None and datetime.fromtimestamp(payload["exp"], timezone.utc) < now:
        raise ValueError("expired oauth state")
    if payload.get("purpose") != PURPOSE or int(payload.get("user_id", 0)) != int(current_user_id):
        raise ValueError("oauth state user mismatch")
    nonce_hash = hashlib.sha256(str(payload["nonce"]).encode()).hexdigest()
    changed = execute(
        "UPDATE google_oauth_states SET used_at=NOW() "
        "WHERE nonce_hash=%s AND user_id=%s AND user_email=%s "
        "AND used_at IS NULL AND expires_at >= NOW()",
        (nonce_hash, int(current_user_id), str(payload["email"])),
    )
    if changed != 1:
        raise ValueError("oauth state already used or expired")
    return payload
```

- [ ] **Step 4: Bind all OAuth entry points to `get_current_user`**

Change `auth_routes.py` so the public signatures are:

```python
@auth_router.get("/login")
async def google_login(request: Request, user: User = Depends(get_current_user)):
    state = await asyncio.to_thread(issue_state, user.id, user.email)
    url = _get_auth_manager().get_auth_url(
        user.email, state=state, redirect_uri=_get_redirect_uri(request)
    )
    return RedirectResponse(url=url)


@auth_router.get("/status")
async def google_auth_status(user: User = Depends(get_current_user)):
    mgr = _get_auth_manager()
    connected = mgr.has_credentials(user.email)
    return {
        "authenticated": connected,
        "google_email": mgr.get_stored_google_email(user.email) if connected else "",
    }


@auth_router.post("/revoke")
async def google_revoke(user: User = Depends(get_current_user)):
    return {"revoked": _get_auth_manager().revoke_credentials(user.email)}
```

The callback must add `user: User = Depends(get_current_user)`, call
`consume_state(state, user.id)` before `exchange_code`, use the decoded email rather than query text,
and render `html.escape(user.email)`. Map invalid/expired state to HTTP 400 without returning the token or exception.

Change `GoogleAuthManager.get_auth_url()` to require the keyword-only `state` and pass it to
`flow.authorization_url(...)`. Change the GWS agent's missing-token marker to:

```python
return (
    "Google Workspace에 접근하려면 Google 계정 연결이 필요합니다.\n\n"
    "잠시 후 Google 로그인 창이 열립니다. 연결 완료 후 같은 질문을 다시 해주세요.\n\n"
    "<!-- gws-auth:/auth/google/login -->"
)
```

Change the frontend marker regex to accept the fixed same-origin path and remove all email query strings:

```javascript
var gwsAuthMatch = cleanContent.match(/<!-- gws-auth:(\/auth\/google\/login) -->/);
fetch("/auth/google/status")
fetch("/auth/google/revoke", { method: "POST" })
window.open("/auth/google/login", "gws_auth", "width=500,height=600");
```

- [ ] **Step 5: Add route-level ownership tests**

Use a small FastAPI app with `auth_router` and `dependency_overrides[get_current_user]`. Assert:

```python
def test_status_ignores_injected_email(client, fake_manager):
    response = client.get("/auth/google/status?user_email=other@example.com")
    assert response.status_code == 200
    assert fake_manager.seen_email == "owner@example.com"


def test_oauth_routes_require_auth(anonymous_client):
    assert anonymous_client.get("/auth/google/status").status_code == 401
    assert anonymous_client.get("/auth/google/login").status_code == 401
    assert anonymous_client.post("/auth/google/revoke").status_code == 401
```

Add this exact regression to `tests/test_gws_gmail.py`:

```python
async def test_missing_gws_token_uses_authenticated_relative_login_route(monkeypatch):
    class MissingAuth:
        def get_credentials(self, _email): return None

    monkeypatch.setattr(gws_agent, "_get_auth_manager", lambda: MissingAuth())
    answer = await gws_agent.GWSAgent().run("오늘 일정", user_email="owner@example.com")
    assert "<!-- gws-auth:/auth/google/login -->" in answer
    assert "owner@example.com" not in answer
    assert "accounts.google.com" not in answer
```

- [ ] **Step 6: Run focused OAuth and GWS regressions**

Run: `pytest tests/test_google_auth_routes.py tests/test_gws_gmail.py -q`

Expected: all tests pass and existing Gmail query/body tests remain green.

- [ ] **Step 7: Commit the secure OAuth boundary**

```bash
git add app/core/google_oauth_state.py app/core/google_auth.py app/api/auth_routes.py app/agents/gws_agent.py app/frontend/chat.js tests/test_google_auth_routes.py tests/test_gws_gmail.py
git commit -m "fix: bind Google OAuth to authenticated users"
```

---

### Task 2: Gmail 메타데이터와 고정 Calendar 구간 수집

**Files:**
- Create: `tests/test_gws_digest.py`
- Modify: `app/core/google_workspace.py`

**Interfaces:**
- Produces: `list_gmail_digest(creds: Credentials, start: datetime, end: datetime, max_results: int = 20) -> dict[str, Any]`
- Produces: `list_calendar_window(creds: Credentials, start: datetime, end: datetime, max_results: int = 50) -> dict[str, Any]`
- Gmail result: `{"items": list[dict], "truncated": bool}`; item keys are `id`, `thread_id`, `subject`, `from`, `received_at`, `unread`, `snippet`, `url`.
- Calendar result: `{"items": list[dict], "truncated": bool}`; item keys are `id`, `summary`, `start`, `end`, `location`, `htmlLink`.

- [ ] **Step 1: Write failing Gmail metadata tests**

```python
# tests/test_gws_digest.py
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.core import google_workspace

SEOUL = ZoneInfo("Asia/Seoul")


def test_digest_uses_metadata_not_full_body(monkeypatch):
    calls = []

    class Req:
        def __init__(self, value): self.value = value
        def execute(self): return self.value

    class Messages:
        def list(self, **kwargs):
            calls.append(("list", kwargs))
            return Req({"messages": [{"id": "m1", "threadId": "t1"}], "nextPageToken": "more"})
        def get(self, **kwargs):
            calls.append(("get", kwargs))
            return Req({
                "id": "m1", "threadId": "t1", "internalDate": "1787619600000",
                "labelIds": ["INBOX", "UNREAD"], "snippet": "결재 확인 부탁드립니다",
                "payload": {"headers": [
                    {"name": "Subject", "value": "결재 요청"},
                    {"name": "From", "value": "Sender <sender@example.com>"},
                ]},
            })

    class Users:
        def __init__(self, messages): self._messages = messages
        def messages(self): return self._messages

    class Service:
        def __init__(self, messages): self._users = Users(messages)
        def users(self): return self._users

    monkeypatch.setattr(google_workspace, "build", lambda *_a, **_k: Service(Messages()))
    result = google_workspace.list_gmail_digest(
        object(),
        datetime(2026, 8, 25, 0, 0, tzinfo=SEOUL),
        datetime(2026, 8, 26, 0, 0, tzinfo=SEOUL),
    )

    get_call = next(kwargs for kind, kwargs in calls if kind == "get")
    assert get_call["format"] == "metadata"
    assert result["truncated"] is True
    assert result["items"][0]["unread"] is True
    assert "body" not in result["items"][0]
```

Add these assertions to the same test:

```python
    list_call = next(kwargs for kind, kwargs in calls if kind == "list")
    assert "after:1787583600" in list_call["q"]
    assert "before:1787670000" in list_call["q"]
    for exclusion in ("-in:spam", "-in:trash", "-in:drafts", "-in:sent", "-from:me"):
        assert exclusion in list_call["q"]
```

- [ ] **Step 2: Write failing Calendar window tests**

```python
def test_calendar_window_uses_exact_bounds_and_reports_truncation(monkeypatch):
    captured = {}

    class Req:
        def execute(self):
            return {"items": [{
                "id": "e1", "summary": "종일 행사",
                "start": {"date": "2026-08-25"}, "end": {"date": "2026-08-26"},
                "htmlLink": "https://www.google.com/calendar/event?eid=e1",
            }], "nextPageToken": "more"}

    class Events:
        def list(self, **kwargs):
            captured.update(kwargs)
            return Req()

    class CalendarService:
        def events(self): return Events()

    monkeypatch.setattr(google_workspace, "build", lambda *_a, **_k: CalendarService())
    start = datetime(2026, 8, 25, 0, 0, tzinfo=SEOUL)
    end = datetime(2026, 9, 1, 0, 0, tzinfo=SEOUL)
    result = google_workspace.list_calendar_window(object(), start, end)

    assert captured["timeMin"].startswith("2026-08-24T15:00:00")
    assert captured["timeMax"].startswith("2026-08-31T15:00:00")
    assert captured["singleEvents"] is True
    assert result["truncated"] is True
```

- [ ] **Step 3: Run digest tests and verify RED**

Run: `pytest tests/test_gws_digest.py -q`

Expected: FAIL because `list_gmail_digest` and `list_calendar_window` do not exist.

- [ ] **Step 4: Implement the two stateless collectors**

```python
def list_gmail_digest(creds, start, end, max_results=20):
    service = build("gmail", "v1", credentials=creds, cache_discovery=False)
    query = (
        f"after:{int(start.timestamp())} before:{int(end.timestamp())} "
        "-in:spam -in:trash -in:drafts -in:sent -from:me"
    )
    page = service.users().messages().list(
        userId="me", q=query, maxResults=max_results
    ).execute()
    items = []
    for ref in page.get("messages", []):
        msg = service.users().messages().get(
            userId="me", id=ref["id"], format="metadata",
            metadataHeaders=["Subject", "From", "Date"],
        ).execute()
        headers = _gmail_part_headers(msg.get("payload", {}))
        message_id = msg.get("id", ref["id"])
        thread_id = msg.get("threadId", ref.get("threadId", message_id))
        items.append({
            "id": message_id,
            "thread_id": thread_id,
            "subject": headers.get("subject", "(제목 없음)"),
            "from": headers.get("from", ""),
            "received_at": datetime.fromtimestamp(
                int(msg.get("internalDate", "0")) / 1000, timezone.utc
            ).isoformat(),
            "unread": "UNREAD" in msg.get("labelIds", []),
            "snippet": msg.get("snippet", "")[:500],
            "url": f"https://mail.google.com/mail/u/0/#all/{message_id}",
        })
    return {"items": items, "truncated": bool(page.get("nextPageToken"))}


def list_calendar_window(creds, start, end, max_results=50):
    service = build("calendar", "v3", credentials=creds, cache_discovery=False)
    page = service.events().list(
        calendarId="primary",
        timeMin=start.astimezone(timezone.utc).isoformat(),
        timeMax=end.astimezone(timezone.utc).isoformat(),
        maxResults=max_results,
        singleEvents=True,
        orderBy="startTime",
    ).execute()
    items = page.get("items", [])
    return {
        "items": [{
            "id": e["id"], "summary": e.get("summary", "(제목 없음)"),
            "start": e.get("start", {}).get("dateTime", e.get("start", {}).get("date", "")),
            "end": e.get("end", {}).get("dateTime", e.get("end", {}).get("date", "")),
            "location": e.get("location", ""), "htmlLink": e.get("htmlLink", ""),
        } for e in items],
        "truncated": bool(page.get("nextPageToken")),
    }
```

Import `timezone` at module top. Do not route either function through `extract_gmail_body()`.

- [ ] **Step 5: Run new and existing Google Workspace tests**

Run: `pytest tests/test_gws_digest.py tests/test_gws_gmail.py -q`

Expected: all pass; the existing `search_gmail()` full-body behavior remains unchanged for explicit chat questions.

- [ ] **Step 6: Commit the lightweight collectors**

```bash
git add app/core/google_workspace.py tests/test_gws_digest.py
git commit -m "feat: add lightweight Workspace digest collectors"
```

---

### Task 3: 사용자별 단일 스냅샷 저장소

**Files:**
- Create: `app/core/personal_briefing_store.py`
- Create: `tests/test_personal_briefing_store.py`

**Interfaces:**
- Produces: `ensure_tables() -> None`
- Produces: `get_snapshot(user_id: int, for_date: date) -> dict[str, Any] | None`
- Produces: `put_snapshot(user_id: int, for_date: date, google_account_hash: str, calendar: dict, mail: dict, priorities: list[dict], generated_at: datetime) -> None`
- Produces: `delete_for_user(user_id: int) -> None`
- Produces: `cleanup(before_date: date) -> int`
- Store never receives or stores `business`, Gmail `snippet`, body, attachment, or OAuth token fields.

- [ ] **Step 1: Write failing storage contract tests**

```python
# tests/test_personal_briefing_store.py
from datetime import date, datetime

from app.core import personal_briefing_store as store


def test_get_snapshot_is_owner_and_date_scoped(monkeypatch):
    seen = {}
    monkeypatch.setattr(store, "fetch_one", lambda sql, p: seen.update(sql=sql, p=p) or None)
    assert store.get_snapshot(7, date(2026, 8, 25)) is None
    assert "user_id = %s" in seen["sql"] and "for_date = %s" in seen["sql"]
    assert seen["p"] == (7, date(2026, 8, 25))


def test_put_snapshot_strips_transient_mail_content(monkeypatch):
    captured = {}
    monkeypatch.setattr(store, "execute", lambda sql, p=(): captured.update(sql=sql, p=p) or 1)
    store.put_snapshot(
        7, date(2026, 8, 25), "hash",
        {"status": "ready", "items": []},
        {"status": "ready", "items": [{"id": "m1", "subject": "S", "snippet": "secret"}]},
        [], datetime(2026, 8, 25, 8, 30),
    )
    assert "secret" not in " ".join(map(str, captured["p"]))
```

Add these exact tests below them:

```python
def test_get_snapshot_decodes_all_json(monkeypatch):
    monkeypatch.setattr(store, "fetch_one", lambda *_a, **_k: {
        "google_account_hash": "h", "calendar_json": '{"status":"ready"}',
        "mail_json": '{"status":"empty"}', "priorities_json": '[]',
        "generated_at": datetime(2026, 8, 25, 8, 30),
    })
    row = store.get_snapshot(7, date(2026, 8, 25))
    assert row["calendar"]["status"] == "ready"
    assert row["mail"]["status"] == "empty"
    assert row["priorities"] == []


def test_delete_and_cleanup_have_narrow_predicates(monkeypatch):
    calls = []
    monkeypatch.setattr(store, "execute", lambda sql, p=(): calls.append((sql, p)) or 1)
    store.delete_for_user(7)
    assert calls[-1][1] == (7,)
    store.cleanup(date(2026, 8, 24))
    assert "for_date < %s" in calls[-1][0]
    assert calls[-1][1] == (date(2026, 8, 24),)
```

- [ ] **Step 2: Run store tests and verify RED**

Run: `pytest tests/test_personal_briefing_store.py -q`

Expected: import fails because `personal_briefing_store.py` does not exist.

- [ ] **Step 3: Implement DDL and store functions**

```python
_DDL = """
CREATE TABLE IF NOT EXISTS personal_briefing_snapshots (
    user_id INT NOT NULL PRIMARY KEY,
    for_date DATE NOT NULL,
    google_account_hash CHAR(64) NOT NULL DEFAULT '',
    calendar_json LONGTEXT NOT NULL,
    mail_json LONGTEXT NOT NULL,
    priorities_json LONGTEXT NOT NULL,
    generated_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_personal_briefing_date (for_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""


def ensure_tables() -> None:
    execute(_DDL)


def _clean_mail(mail: dict[str, Any]) -> dict[str, Any]:
    safe = dict(mail)
    safe["items"] = [
        {k: v for k, v in item.items() if k not in {"snippet", "body", "payload"}}
        for item in mail.get("items", [])
    ]
    return safe


def get_snapshot(user_id: int, for_date: date) -> dict[str, Any] | None:
    row = fetch_one(
        "SELECT google_account_hash,calendar_json,mail_json,priorities_json,generated_at "
        "FROM personal_briefing_snapshots WHERE user_id = %s AND for_date = %s",
        (int(user_id), for_date),
    )
    if not row:
        return None
    return {
        "google_account_hash": row["google_account_hash"],
        "calendar": json.loads(row["calendar_json"]),
        "mail": json.loads(row["mail_json"]),
        "priorities": json.loads(row["priorities_json"]),
        "generated_at": row["generated_at"],
    }
```

Implement `put_snapshot()` with the complete owner-keyed upsert:

```python
def put_snapshot(user_id, for_date, google_account_hash, calendar, mail, priorities, generated_at):
    execute(
        "INSERT INTO personal_briefing_snapshots "
        "(user_id,for_date,google_account_hash,calendar_json,mail_json,priorities_json,generated_at) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s) "
        "ON DUPLICATE KEY UPDATE for_date=VALUES(for_date),"
        "google_account_hash=VALUES(google_account_hash),calendar_json=VALUES(calendar_json),"
        "mail_json=VALUES(mail_json),priorities_json=VALUES(priorities_json),"
        "generated_at=VALUES(generated_at)",
        (
            int(user_id), for_date, google_account_hash,
            json.dumps(calendar, ensure_ascii=False),
            json.dumps(_clean_mail(mail), ensure_ascii=False),
            json.dumps(priorities, ensure_ascii=False), generated_at,
        ),
    )
```

Implement deletion and cleanup as:

```python
def delete_for_user(user_id: int) -> None:
    execute("DELETE FROM personal_briefing_snapshots WHERE user_id = %s", (int(user_id),))


def cleanup(before_date: date) -> int:
    return int(execute("DELETE FROM personal_briefing_snapshots WHERE for_date < %s", (before_date,)) or 0)
```

- [ ] **Step 4: Run store tests**

Run: `pytest tests/test_personal_briefing_store.py -q`

Expected: all pass, including the negative assertion that serialized parameters contain no `snippet` value.

- [ ] **Step 5: Commit the snapshot store**

```bash
git add app/core/personal_briefing_store.py tests/test_personal_briefing_store.py
git commit -m "feat: add owner-scoped personal briefing snapshots"
```

---

### Task 4: 개인 브리핑 집계·메일 요약 검증·우선 확인

**Files:**
- Create: `app/core/personal_briefing.py`
- Create: `tests/test_personal_briefing.py`

**Interfaces:**
- Consumes: Task 2 `list_gmail_digest`, `list_calendar_window`.
- Consumes: Task 3 `get_snapshot`, `put_snapshot`, `cleanup`.
- Produces: `get_cached_for_user(user: User, now: datetime | None = None) -> dict[str, Any]`
- Produces: `async refresh_for_user(user: User, now: datetime | None = None, force: bool = False) -> dict[str, Any]`
- Produces: `async run_morning_precompute(now: datetime | None = None) -> dict[str, int]`
- Envelope keys are exactly `enabled`, `for_date`, `timezone`, `generated_at`, `needs_refresh`, `google`, `priorities`, `calendar`, `mail`, `business`.

- [ ] **Step 1: Write failing KST, summary grounding, and priority tests**

```python
# tests/test_personal_briefing.py
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.core import personal_briefing as pb

SEOUL = ZoneInfo("Asia/Seoul")


def test_window_is_today_plus_six_days():
    day, start, end = pb.briefing_window(datetime(2026, 8, 25, 16, 0, tzinfo=SEOUL))
    assert str(day) == "2026-08-25"
    assert start.isoformat() == "2026-08-25T00:00:00+09:00"
    assert end.isoformat() == "2026-09-01T00:00:00+09:00"


def test_summary_drops_unknown_message_ids(monkeypatch):
    monkeypatch.setattr(pb, "_generate_mail_json", lambda _items: {
        "summary": "결재 요청이 있습니다.",
        "action_candidates": [
            {"message_id": "real", "reason": "확인 요청"},
            {"message_id": "invented", "reason": "없는 메일"},
        ],
    })
    items = [{"id": "real", "subject": "결재", "from": "A", "snippet": "확인"}]
    result = pb.summarize_mail(items)
    assert [x["message_id"] for x in result["action_candidates"]] == ["real"]


def test_priorities_reference_real_sources_only():
    result = pb.build_priorities(
        calendar={"status": "ready", "items": [{
            "id": "e1", "title": "회의", "start": "2026-08-25T10:00:00+09:00",
            "ended": False, "url": "https://calendar.google.com/event?eid=e1",
        }]},
        mail={"status": "ready", "items": [{"id": "m1", "subject": "결재", "url": "https://mail.google.com/mail/u/0/#all/m1"}],
              "action_candidates": [{"message_id": "m1", "reason": "확인 요청"}]},
        business={"status": "ready", "item": {"id": "b1", "title": "매출 변화", "follow_up": "자세히"}},
        now=datetime(2026, 8, 25, 9, 0, tzinfo=SEOUL),
    )
    assert [x["source"] for x in result] == ["calendar", "mail", "business"]
    assert all(x["source_id"] in {"e1", "m1", "b1"} for x in result)
```

Add these exact edge tests:

```python
def test_business_opt_out_hides_old_content(monkeypatch):
    monkeypatch.setattr(pb.briefing, "is_opted_out", lambda _uid: True)
    monkeypatch.setattr(pb.briefing, "for_user", lambda *_a, **_k: [{"title": "old secret"}])
    result = pb._business_for_user(7)
    assert result == {"status": "disabled", "item": None}


def test_past_today_event_is_marked_ended():
    raw = {"items": [{
        "id": "e1", "summary": "아침 회의", "start": "2026-08-25T08:00:00+09:00",
        "end": "2026-08-25T09:00:00+09:00", "location": "", "htmlLink": "",
    }], "truncated": False}
    section = pb._normalize_calendar(raw, datetime(2026, 8, 25, 10, 0, tzinfo=SEOUL))
    assert section["items"][0]["ended"] is True


def test_failed_section_reuses_only_same_day_cache_as_stale():
    previous = {"status": "ready", "items": [{"id": "e1"}], "error_code": ""}
    stale = pb._merge_failed_section(previous, "google_timeout")
    assert stale["status"] == "stale"
    assert stale["items"] == [{"id": "e1"}]
    assert stale["error_code"] == "google_timeout"
    assert pb._merge_failed_section(None, "google_timeout")["status"] == "error"


def test_cache_age_boundary_is_ten_minutes():
    now = datetime(2026, 8, 25, 9, 0, tzinfo=SEOUL)
    assert pb._needs_refresh(now - pb.CACHE_TTL, now) is True
    assert pb._needs_refresh(now - pb.CACHE_TTL + timedelta(seconds=1), now) is False


@pytest.mark.asyncio
async def test_summary_timeout_keeps_deterministic_mail_items(monkeypatch):
    async def timeout(*_a, **_k): raise asyncio.TimeoutError()
    monkeypatch.setattr(pb, "_summarize_mail_async", timeout)
    mail = {"status": "ready", "items": [{"id": "m1", "subject": "제목", "snippet": "본문"}],
            "count_label": "1건", "unread": 1, "truncated": False, "error_code": ""}
    result = await pb._attach_summary(mail)
    assert result["status"] == "ready"
    assert result["error_code"] == "summary_failed"
    assert result["items"][0]["subject"] == "제목"
    assert "snippet" not in result["items"][0]
```

Import `asyncio` and `pytest` at the top of the test file. Add this exact date-scope test:

```python
def test_cached_lookup_requests_only_today(monkeypatch):
    seen = {}
    monkeypatch.setattr(pb._auth_manager, "has_credentials", lambda _email: False)
    monkeypatch.setattr(pb._auth_manager, "get_stored_google_email", lambda _email: "")
    monkeypatch.setattr(pb.store, "get_snapshot", lambda uid, day: seen.update(uid=uid, day=day) or None)
    monkeypatch.setattr(pb, "_business_for_user", lambda _uid: {"status": "empty", "item": None})
    user = type("U", (), {"id": 7, "email": "owner@example.com"})()
    pb.get_cached_for_user(user, datetime(2026, 8, 25, 9, 0, tzinfo=SEOUL))
    assert seen == {"uid": 7, "day": date(2026, 8, 25)}
```

Import `date` with `datetime` and `timedelta` in the test file.

- [ ] **Step 2: Run core tests and verify RED**

Run: `pytest tests/test_personal_briefing.py -q`

Expected: import fails because `personal_briefing.py` does not exist.

- [ ] **Step 3: Implement deterministic date and normalization helpers**

```python
SEOUL = ZoneInfo("Asia/Seoul")
CACHE_TTL = timedelta(minutes=10)


def briefing_window(now: datetime | None = None):
    current = (now or datetime.now(SEOUL)).astimezone(SEOUL)
    day = current.date()
    start = datetime.combine(day, time.min, tzinfo=SEOUL)
    return day, start, start + timedelta(days=7)


def _parse_event_time(value: str) -> datetime:
    if len(value) == 10:
        return datetime.fromisoformat(value).replace(tzinfo=SEOUL)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.replace(tzinfo=SEOUL) if parsed.tzinfo is None else parsed.astimezone(SEOUL)


def _event_has_ended(end_value: str, now: datetime) -> bool:
    return _parse_event_time(end_value) <= now.astimezone(SEOUL)


def _normalize_calendar(raw: dict, now: datetime) -> dict:
    items = []
    for event in raw.get("items", []):
        start_value = event["start"]
        all_day = len(start_value) == 10
        items.append({
            "id": event["id"], "title": event["summary"], "start": event["start"],
            "end": event["end"], "all_day": all_day, "location": event.get("location", ""),
            "url": event.get("htmlLink", ""), "ended": _event_has_ended(event["end"], now),
        })
    return {"status": "ready" if items else "empty", "items": items,
            "truncated": raw.get("truncated", False), "error_code": ""}


def _normalize_mail(raw: dict) -> dict:
    items = [{
        "id": item["id"], "thread_id": item["thread_id"],
        "subject": item["subject"], "from_display": item["from"],
        "received_at": item["received_at"], "unread": bool(item["unread"]),
        "snippet": item.get("snippet", ""), "url": item["url"],
    } for item in raw.get("items", [])]
    truncated = bool(raw.get("truncated"))
    return {
        "status": "ready" if items else "empty",
        "count_label": f"{len(items)}건 이상" if truncated else f"{len(items)}건",
        "unread": sum(1 for item in items if item["unread"]),
        "summary": "", "action_candidates": [], "items": items,
        "truncated": truncated, "error_code": "",
    }
```

`snippet` exists only between `_normalize_mail()` and `_attach_summary()`. `_attach_summary()` must strip it even when
the LLM fails before the section reaches the API or store.

- [ ] **Step 4: Implement grounded Flash JSON summarization**

```python
def _generate_mail_json(items: list[dict]) -> dict:
    prompt_items = [{
        "message_id": item["id"], "subject": item["subject"],
        "from": item["from"], "snippet": item.get("snippet", ""),
    } for item in items]
    prompt = (
        "다음 오늘 받은 메일 미리보기만 사용하세요. 전체 요약은 2문장 이하, "
        "확인 후보는 최대 3개로 JSON을 반환하세요. 후보 message_id는 입력값만 복사하세요. "
        "건수·시간·사람·마감일을 추측하지 마세요.\n" +
        json.dumps(prompt_items, ensure_ascii=False)
    )
    text = get_flash_client().generate_json(prompt, temperature=0.0, max_output_tokens=1200)
    value = json.loads(text)
    return {
        "summary": str(value.get("summary", ""))[:500],
        "action_candidates": list(value.get("action_candidates", []))[:3],
    }


def summarize_mail(items: list[dict]) -> dict:
    generated = _generate_mail_json(items) if items else {"summary": "", "action_candidates": []}
    by_id = {item["id"]: item for item in items}
    valid = []
    for candidate in generated.get("action_candidates", []):
        message_id = str(candidate.get("message_id", ""))
        if message_id in by_id:
            valid.append({"message_id": message_id, "reason": str(candidate.get("reason", ""))[:160]})
    return {"summary": generated.get("summary", ""), "action_candidates": valid[:3]}


async def _summarize_mail_async(items: list[dict]) -> dict:
    return await asyncio.wait_for(
        asyncio.to_thread(summarize_mail, items), timeout=8.0
    )


async def _attach_summary(mail: dict) -> dict:
    result = dict(mail)
    try:
        summary = await _summarize_mail_async(result.get("items", []))
        result.update(summary)
    except Exception:
        result["summary"] = ""
        result["action_candidates"] = []
        result["error_code"] = "summary_failed"
    result["items"] = [
        {k: v for k, v in item.items() if k != "snippet"}
        for item in result.get("items", [])
    ]
    return result
```

The broad exception is intentional only around optional summary formatting. It must not swallow Gmail collection
errors, which are handled per section by refresh.

Implement priority composition as a fixed source round rather than an LLM ranking:

```python
def build_priorities(calendar: dict, mail: dict, business: dict, now: datetime) -> list[dict]:
    priorities = []
    upcoming = [item for item in calendar.get("items", []) if not item.get("ended")]
    upcoming.sort(key=lambda item: _parse_event_time(item["start"]))
    if upcoming and _parse_event_time(upcoming[0]["start"]) <= now.astimezone(SEOUL) + timedelta(hours=24):
        event = upcoming[0]
        priorities.append({"source": "calendar", "source_id": event["id"],
                           "title": event["title"], "reason": "24시간 안에 시작", "url": event["url"]})

    mail_by_id = {item["id"]: item for item in mail.get("items", [])}
    if mail.get("action_candidates"):
        candidate = mail["action_candidates"][0]
        message = mail_by_id.get(candidate["message_id"])
        if message:
            priorities.append({"source": "mail", "source_id": message["id"],
                               "title": message["subject"], "reason": candidate["reason"],
                               "url": message["url"]})

    item = business.get("item") if business.get("status") == "ready" else None
    if item:
        priorities.append({"source": "business", "source_id": str(item["id"]),
                           "title": item["title"], "reason": "최근 업무 지표 변화",
                           "url": "", "follow_up": item.get("follow_up", "")})
    return priorities[:3]
```

- [ ] **Step 5: Implement cached GET envelope and partial refresh**

Use these exact helpers and cached envelope rules:

```python
_locks: dict[int, asyncio.Lock] = {}
_auth_manager = GoogleAuthManager()


def _account_hash(value: str) -> str:
    return hashlib.sha256((value or "").strip().lower().encode()).hexdigest()


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=SEOUL) if value.tzinfo is None else value.astimezone(SEOUL)


def _needs_refresh(generated_at: datetime | None, now: datetime) -> bool:
    return generated_at is None or _aware(generated_at) <= now.astimezone(SEOUL) - CACHE_TTL


def _business_for_user(user_id: int) -> dict:
    if briefing.is_opted_out(user_id):
        return {"status": "disabled", "item": None}
    rows = briefing.for_user(user_id, limit=1)
    if not rows:
        return {"status": "empty", "item": None}
    row = rows[0]
    return {"status": "ready", "item": {
        "id": str(row["id"]), "for_date": str(row["for_date"]),
        "title": row.get("title", ""), "body": row.get("body", ""),
        "follow_up": row.get("follow_up", ""),
    }}


def _merge_failed_section(previous: dict | None, error_code: str) -> dict:
    if previous:
        result = dict(previous)
        result.update(status="stale", error_code=error_code)
        return result
    return {"status": "error", "items": [], "truncated": False, "error_code": error_code}


def get_cached_for_user(user: User, now: datetime | None = None) -> dict:
    current = (now or datetime.now(SEOUL)).astimezone(SEOUL)
    day, _start, _end = briefing_window(current)
    connected = _auth_manager.has_credentials(user.email)
    account = _auth_manager.get_stored_google_email(user.email) if connected else ""
    snapshot = store.get_snapshot(user.id, day)
    if not connected:
        snapshot = None
    if snapshot and snapshot["google_account_hash"] != _account_hash(account or user.email):
        snapshot = None
    if snapshot:
        calendar, mail, priorities = snapshot["calendar"], snapshot["mail"], snapshot["priorities"]
        generated = snapshot["generated_at"]
    else:
        status = "disconnected" if not connected else "empty"
        calendar = {"status": status, "items": [], "truncated": False, "error_code": "oauth_missing" if not connected else ""}
        mail = {"status": status, "count_label": "0건", "unread": 0, "summary": "",
                "action_candidates": [], "items": [], "truncated": False,
                "error_code": "oauth_missing" if not connected else ""}
        priorities, generated = [], None
    return {
        "enabled": True, "for_date": str(day), "timezone": "Asia/Seoul",
        "generated_at": _aware(generated).isoformat() if generated else "",
        "needs_refresh": connected and _needs_refresh(generated, current),
        "google": {"connected": connected, "account": account},
        "priorities": priorities, "calendar": calendar, "mail": mail,
        "business": _business_for_user(user.id),
    }
```

Implement refresh with the same envelope and per-section merging:

```python
async def _collect_with_timeout(fn, *args):
    return await asyncio.wait_for(asyncio.to_thread(fn, *args), timeout=10.0)


async def refresh_for_user(user: User, now: datetime | None = None, force: bool = False) -> dict:
    current = (now or datetime.now(SEOUL)).astimezone(SEOUL)
    lock = _locks.setdefault(user.id, asyncio.Lock())
    async with lock:
        cached = await asyncio.to_thread(get_cached_for_user, user, current)
        if not force and not cached["needs_refresh"]:
            return cached
        creds = await asyncio.to_thread(_auth_manager.get_credentials, user.email)
        if creds is None:
            cached["calendar"] = (
                _merge_failed_section(cached["calendar"], "oauth_expired")
                if cached["calendar"].get("items") else
                {"status": "disconnected", "items": [], "truncated": False, "error_code": "oauth_expired"}
            )
            cached["mail"] = (
                _merge_failed_section(cached["mail"], "oauth_expired")
                if cached["mail"].get("items") else
                {"status": "disconnected", "count_label": "0건", "unread": 0,
                 "summary": "", "action_candidates": [], "items": [],
                 "truncated": False, "error_code": "oauth_expired"}
            )
            cached["google"]["connected"] = False
            cached["needs_refresh"] = False
            return cached

        day, start, end = briefing_window(current)
        calendar_raw, mail_raw = await asyncio.gather(
            _collect_with_timeout(list_calendar_window, creds, start, end, 50),
            _collect_with_timeout(list_gmail_digest, creds, start, current, 20),
            return_exceptions=True,
        )
        old = await asyncio.to_thread(store.get_snapshot, user.id, day)
        previous_calendar = old.get("calendar") if old else None
        previous_mail = old.get("mail") if old else None
        calendar = (
            _merge_failed_section(previous_calendar, _error_code(calendar_raw))
            if isinstance(calendar_raw, BaseException)
            else _normalize_calendar(calendar_raw, current)
        )
        if isinstance(mail_raw, BaseException):
            mail = _merge_failed_section(previous_mail, _error_code(mail_raw))
            mail.setdefault("count_label", "0건")
            mail.setdefault("unread", 0)
            mail.setdefault("summary", "")
            mail.setdefault("action_candidates", [])
        else:
            mail = await _attach_summary(_normalize_mail(mail_raw))
        business = await asyncio.to_thread(_business_for_user, user.id)
        priorities = build_priorities(calendar, mail, business, current)
        account = _auth_manager.get_stored_google_email(user.email) or user.email
        generated_at = current.replace(tzinfo=None)
        await asyncio.to_thread(
            store.put_snapshot, user.id, day, _account_hash(account),
            calendar, mail, priorities, generated_at,
        )
        return {
            "enabled": True, "for_date": str(day), "timezone": "Asia/Seoul",
            "generated_at": current.isoformat(), "needs_refresh": False,
            "google": {"connected": True, "account": account},
            "priorities": priorities, "calendar": calendar, "mail": mail, "business": business,
        }
```

Use these error codes only:

```python
def _error_code(exc: BaseException) -> str:
    text = str(exc).lower()
    if "quota" in text or "429" in text: return "google_quota"
    if "refresh" in text or "invalid_grant" in text: return "oauth_expired"
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError)): return "google_timeout"
    return "google_error"
```

Do not include `str(exc)` in the response or logs.

- [ ] **Step 6: Implement 08:30 precompute with concurrency 3**

```python
async def run_morning_precompute(now: datetime | None = None) -> dict[str, int]:
    rows = await asyncio.to_thread(
        fetch_all,
        "SELECT u.id,COALESCE(a.email,u.email) email,COALESCE(a.display_name,u.display_name) name,"
        "COALESCE(a.department,'') department,u.role,u.allowed_models,u.ad_user_id "
        "FROM users u LEFT JOIN ad_users a ON a.id=u.ad_user_id "
        "WHERE u.is_active=1 AND u.last_login >= DATE_SUB(NOW(), INTERVAL 30 DAY) "
        "AND COALESCE(a.department,'') NOT LIKE %s",
        ("%퇴사%",),
    )
    semaphore = asyncio.Semaphore(3)
    selected = [row for row in rows if GoogleAuthManager().has_credentials(row["email"])]

    async def one(row):
        async with semaphore:
            user = User(id=row["id"], email=row["email"], name=row["name"],
                        department=row["department"], role=row["role"],
                        allowed_models=row["allowed_models"], ad_user_id=row["ad_user_id"])
            return await refresh_for_user(user, now=now, force=True)

    results = await asyncio.gather(*(one(row) for row in selected), return_exceptions=True)
    await asyncio.to_thread(cleanup, briefing_window(now)[0] - timedelta(days=1))
    return {"selected": len(selected), "succeeded": sum(not isinstance(x, Exception) for x in results),
            "failed": sum(isinstance(x, Exception) for x in results)}
```

Reuse one `GoogleAuthManager` instance rather than constructing it inside the list comprehension in the final code.

- [ ] **Step 7: Run core and prerequisite tests**

Run: `pytest tests/test_personal_briefing.py tests/test_personal_briefing_store.py tests/test_gws_digest.py tests/test_briefing.py -q`

Expected: all pass. The existing deterministic sales briefing tests must remain unchanged.

- [ ] **Step 8: Commit the aggregator**

```bash
git add app/core/personal_briefing.py tests/test_personal_briefing.py
git commit -m "feat: aggregate cached personal work briefings"
```

---

### Task 5: JWT API, feature flag, scheduler, startup DDL, self-check

**Files:**
- Create: `app/api/personal_briefing_api.py`
- Create: `tests/test_personal_briefing_api.py`
- Modify: `app/config.py:33-145`
- Modify: `app/api/auth_routes.py:157-174`
- Modify: `app/main.py:20-29,90-226,260-275,687-709`
- Modify: `app/core/self_check.py:162-176`

**Interfaces:**
- Consumes: Task 1 `ensure_oauth_state_table`; Task 3 `ensure_tables`, `delete_for_user`; Task 4 cached/refresh/precompute functions.
- HTTP GET `/api/personal-briefing` returns the common envelope or `{"enabled": false}`.
- HTTP POST `/api/personal-briefing/refresh` returns the refreshed common envelope; disabled mode returns 404.
- Scheduler ID: `personal_briefing_daily` at 08:30 KST/server local time.

- [ ] **Step 1: Write failing API ownership and feature flag tests**

```python
# tests/test_personal_briefing_api.py
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.auth_middleware import get_current_user
from app.api.personal_briefing_api import router
from app.db.models import User

OWNER = User(id=7, email="owner@example.com", name="Owner", department="D", role="user", allowed_models="skin1004-Analysis")


def test_get_uses_dependency_user_only(monkeypatch):
    seen = {}
    def fake_cached(user):
        seen["user_id"] = user.id
        return {"enabled": True}
    monkeypatch.setattr("app.api.personal_briefing_api.get_cached_for_user", fake_cached)
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: OWNER
    response = TestClient(app).get("/api/personal-briefing?user_id=8&user_email=other@example.com")
    assert response.status_code == 200
    assert seen["user_id"] == 7


def test_refresh_requires_auth():
    app = FastAPI()
    app.include_router(router)
    assert TestClient(app).post("/api/personal-briefing/refresh").status_code == 401
```

Add the disabled-setting test with a core function that fails if invoked:

```python
def test_disabled_flag_short_circuits_core(monkeypatch):
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: OWNER
    monkeypatch.setattr(
        "app.api.personal_briefing_api.get_settings",
        lambda: type("S", (), {"personal_briefing_enabled": False})(),
    )
    monkeypatch.setattr(
        "app.api.personal_briefing_api.get_cached_for_user",
        lambda _user: (_ for _ in ()).throw(AssertionError("core called")),
    )
    client = TestClient(app)
    assert client.get("/api/personal-briefing").json() == {"enabled": False}
    assert client.post("/api/personal-briefing/refresh").status_code == 404
```

- [ ] **Step 2: Run API tests and verify RED**

Run: `pytest tests/test_personal_briefing_api.py -q`

Expected: import fails because `personal_briefing_api.py` does not exist.

- [ ] **Step 3: Implement API and feature flag**

Add to `Settings`:

```python
personal_briefing_enabled: bool = True
```

Create the router:

```python
router = APIRouter(prefix="/api/personal-briefing", tags=["personal-briefing"])


@router.get("")
async def get_personal_briefing(user: User = Depends(get_current_user)) -> dict:
    if not get_settings().personal_briefing_enabled:
        return {"enabled": False}
    return await asyncio.to_thread(get_cached_for_user, user)


@router.post("/refresh")
async def refresh_personal_briefing(user: User = Depends(get_current_user)) -> dict:
    if not get_settings().personal_briefing_enabled:
        raise HTTPException(status_code=404, detail="Personal briefing disabled")
    try:
        return await asyncio.wait_for(refresh_for_user(user), timeout=15.0)
    except asyncio.TimeoutError:
        cached = await asyncio.to_thread(get_cached_for_user, user)
        for key in ("calendar", "mail"):
            if cached[key]["status"] in {"empty", "ready"} and not cached[key].get("items"):
                cached[key]["status"] = "error"
                cached[key]["error_code"] = "google_timeout"
        cached["needs_refresh"] = False
        return cached
```

- [ ] **Step 4: Register DDL, router, scheduler, and self-check**

In application lifespan call both table initializers via `asyncio.to_thread`:

```python
from app.core.google_oauth_state import ensure_oauth_state_table
from app.core.personal_briefing_store import ensure_tables as ensure_personal_briefing_tables
await asyncio.to_thread(ensure_oauth_state_table)
await asyncio.to_thread(ensure_personal_briefing_tables)
```

Register `personal_briefing_router`, then add:

```python
_scheduler.add_job(
    _personal_briefing_job, "cron", hour=8, minute=30, id="personal_briefing_daily"
)


async def _personal_briefing_job():
    from app.core.self_check import track_job
    with track_job("personal_briefing_daily") as jr:
        if not get_settings().personal_briefing_enabled:
            jr.set_note("기능 플래그 꺼짐")
            return
        from app.core.personal_briefing import run_morning_precompute
        result = await run_morning_precompute()
        jr.set_note(
            f"대상 {result['selected']}명 · 성공 {result['succeeded']}명 · 실패 {result['failed']}명"
        )
```

Wrap the job in try/except and log only counts/error class. Add to `EXPECTED_JOBS`:

```python
"personal_briefing_daily": (26, "로그인 개인 업무 브리핑 선계산 (08:30)"),
```

- [ ] **Step 5: Delete snapshots on OAuth revoke**

After the Task 1 token deletion, add:

```python
deleted = _get_auth_manager().revoke_credentials(user.email)
await asyncio.to_thread(delete_for_user, user.id)
return {"revoked": deleted}
```

The snapshot is deleted even if the token file was already absent.

- [ ] **Step 6: Add source-based registration regressions**

```python
def test_personal_briefing_job_is_monitored():
    from app.core.self_check import EXPECTED_JOBS
    assert "personal_briefing_daily" in EXPECTED_JOBS


def test_main_registers_personal_briefing_router_and_schedule():
    import inspect
    from app import main
    src = inspect.getsource(main.create_app)
    assert "personal_briefing_router" in src
    assert 'id="personal_briefing_daily"' in src
```

- [ ] **Step 7: Run backend feature tests**

Run: `pytest tests/test_personal_briefing_api.py tests/test_personal_briefing.py tests/test_google_auth_routes.py tests/test_briefing.py -q`

Expected: all pass, including unauthorized 401 and disabled-mode short circuit.

- [ ] **Step 8: Commit the API and schedule**

```bash
git add app/api/personal_briefing_api.py app/config.py app/api/auth_routes.py app/main.py app/core/self_check.py tests/test_personal_briefing_api.py
git commit -m "feat: expose and precompute personal briefings"
```

---

### Task 6: 로그인 웰컴 카드와 비동기 갱신 UI

**Files:**
- Create: `app/frontend/personal-briefing.js`
- Create: `tests/frontend/test_personal_briefing_welcome.py`
- Modify: `app/frontend/chat.html:14,149-169,443`
- Modify: `app/frontend/chat.js:662-712,1015-1055,2830-2834,3958-4023`
- Modify: `app/static/style.css:960-1050,3998-4060`

**Interfaces:**
- Produces: `window.CellaPersonalBriefing.create({root, input, connect, fetchImpl}) -> controller`
- Controller: `load() -> Promise<void>`, `show() -> void`, `refreshAfterConnect() -> Promise<void>`.
- `chat.js` keeps one controller in `personalBriefingController` and calls `show()` only from `showWelcome()`.

- [ ] **Step 1: Write failing browser tests for cached-first rendering and safe text**

```python
# tests/frontend/test_personal_briefing_welcome.py
from pathlib import Path
import pytest
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "app/frontend/personal-briefing.js"
STYLE = ROOT / "app/static/style.css"


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as playwright:
        instance = playwright.chromium.launch()
        yield instance
        instance.close()


@pytest.fixture
def page(browser):
    context = browser.new_context()
    current = context.new_page()
    yield current
    context.close()


def test_cached_cards_render_and_long_titles_have_hover_text(page):
    page.set_content('''
      <section id="personal-briefing"></section>
      <textarea id="chat-input"></textarea>
      <button id="connect"></button>
    ''')
    page.add_script_tag(path=str(SCRIPT))
    page.evaluate("""() => {
      const payload = {
        enabled: true, for_date: '2026-08-25', generated_at: '2026-08-25T08:30:00+09:00',
        needs_refresh: false, google: {connected: true, account: 'me@example.com'}, priorities: [],
        calendar: {status: 'ready', items: [{id:'e1', title:'아주 긴 주간 회의 전체 제목', start:'2026-08-25T10:00:00+09:00', end:'2026-08-25T11:00:00+09:00', all_day:false, location:'', url:'', ended:false}]},
        mail: {status:'empty', count_label:'0건', unread:0, summary:'', action_candidates:[], items:[]},
        business: {status:'empty', item:null}
      };
      const fetchImpl = async () => ({ok:true, json: async () => payload});
      window.controller = CellaPersonalBriefing.create({
        root: document.querySelector('#personal-briefing'), input: document.querySelector('#chat-input'),
        connect: () => {}, fetchImpl
      });
      return window.controller.load();
    }""")
    item = page.locator(".personal-briefing-item").first
    assert item.inner_text() == "아주 긴 주간 회의 전체 제목"
    assert item.get_attribute("title") == "아주 긴 주간 회의 전체 제목"
```

Add these renderer safety assertions:

```python
def test_renderer_treats_google_text_as_text_and_rejects_bad_urls(page):
    page.set_content('<section id="personal-briefing"></section><textarea id="chat-input"></textarea>')
    page.add_script_tag(path=str(SCRIPT))
    result = page.evaluate("""() => ({
      js: CellaPersonalBriefing.safeUrl('javascript:alert(1)'),
      evil: CellaPersonalBriefing.safeUrl('https://evil.example/x'),
      mail: CellaPersonalBriefing.safeUrl('https://mail.google.com/mail/u/0/#all/m1')
    })""")
    assert result == {"js": "", "evil": "", "mail": "https://mail.google.com/mail/u/0/#all/m1"}
    page.evaluate("""() => {
      const el = document.createElement('div');
      el.className = 'personal-briefing-item';
      el.textContent = '<img src=x onerror=window.xss=1>';
      document.querySelector('#personal-briefing').appendChild(el);
    }""")
    assert page.locator("#personal-briefing img").count() == 0
    assert page.evaluate("window.xss") is None
```

- [ ] **Step 2: Write failing state and responsive tests**

Use a two-response fetch fake and the shipped CSS:

```python
def test_stale_get_is_replaced_by_refresh_and_mobile_is_one_column(page):
    page.set_viewport_size({"width": 390, "height": 844})
    page.set_content('''
      <section class="personal-briefing" id="personal-briefing">
        <div class="personal-briefing-grid" id="personal-briefing-grid"></div>
      </section><textarea id="chat-input"></textarea>
    ''')
    page.add_style_tag(path=str(STYLE))
    page.add_script_tag(path=str(SCRIPT))
    page.evaluate("""() => {
      const fixture = (status, subject, needsRefresh) => ({
        enabled:true, for_date:'2026-08-25', generated_at:'2026-08-25T08:30:00+09:00',
        needs_refresh:needsRefresh, google:{connected:true, account:'me@example.com'}, priorities:[],
        calendar:{status:'empty',items:[],truncated:false,error_code:''},
        mail:{status:status,count_label:'1건',unread:1,summary:'',action_candidates:[],
              items:[{id:'m1',thread_id:'t1',subject:subject,from_display:'A',
                      received_at:'2026-08-25T08:00:00+09:00',unread:true,url:''}],
              truncated:false,error_code:''},
        business:{status:'empty',item:null}
      });
      const stale = fixture('stale', '저장본 메일', true);
      const fresh = fixture('ready', '최신 메일', false);
      let calls = 0;
      const fetchImpl = async () => ({ok:true, json:async () => (++calls === 1 ? stale : fresh)});
      window.controller = CellaPersonalBriefing.create({
        root: document.querySelector('#personal-briefing'), input: document.querySelector('#chat-input'),
        connect: () => {}, fetchImpl
      });
      return controller.load();
    }""")
    assert "최신 메일" in page.locator("#personal-briefing").inner_text()
    columns = page.locator("#personal-briefing-grid").evaluate("el => getComputedStyle(el).gridTemplateColumns")
    assert len(columns.split()) == 1
```

Add a source regression in the same test module:

```python
def test_existing_conversation_still_hides_welcome():
    source = (ROOT / "app/frontend/chat.js").read_text(encoding="utf-8")
    briefing_source = SCRIPT.read_text(encoding="utf-8")
    block = source[source.index("async function loadConversation"):source.index("async function saveMessage")]
    assert 'chatWelcome.style.display = "none"' in block
    assert "personalBriefingController.show()" not in block
    assert "localStorage" not in briefing_source
```

- [ ] **Step 3: Run browser tests and verify RED**

Run: `pytest tests/frontend/test_personal_briefing_welcome.py -q`

Expected: FAIL because `personal-briefing.js` does not exist.

- [ ] **Step 4: Add semantic HTML container and script load**

Inside `chat-welcome`, between greeting and existing suggestions, add:

```html
<section class="personal-briefing" id="personal-briefing" aria-labelledby="personal-briefing-title">
  <div class="personal-briefing-heading">
    <h2 id="personal-briefing-title">오늘 브리핑</h2>
    <span id="personal-briefing-updated" aria-live="polite">불러오는 중</span>
  </div>
  <div class="personal-briefing-grid" id="personal-briefing-grid" aria-live="polite"></div>
</section>
```

Load `/frontend/personal-briefing.js?v=1` before `chat.js`. Increment `style.css?v=169` to `v=170` and
`chat.js?v=261` to `v=262`.

- [ ] **Step 5: Implement a DOM-only renderer and controller**

The module must use `document.createElement()` and `textContent`; do not interpolate API text into HTML.

```javascript
(function () {
  "use strict";

  var ALLOWED = {
    "mail.google.com": true,
    "calendar.google.com": true
  };
  var STATUS_LABEL = {
    loading: "준비 중", ready: "최신", stale: "저장본", disconnected: "연결 필요",
    empty: "결과 없음", error: "오류", disabled: "꺼짐"
  };

  function safeUrl(value) {
    try {
      var url = new URL(value, window.location.origin);
      if (ALLOWED[url.hostname]) return url.href;
      if (url.hostname === "www.google.com" && url.pathname.indexOf("/calendar/") === 0) return url.href;
    } catch (_e) {}
    return "";
  }

  function createText(tag, className, value) {
    var el = document.createElement(tag);
    el.className = className;
    el.textContent = value || "";
    return el;
  }

  function addItem(list, label, url, question, options) {
    var href = safeUrl(url || "");
    var item = document.createElement(href ? "a" : "button");
    item.className = "personal-briefing-item";
    item.textContent = label || "(제목 없음)";
    item.title = label || "(제목 없음)";
    if (href) {
      item.href = href;
      item.target = "_blank";
      item.rel = "noopener noreferrer";
    } else {
      item.type = "button";
      item.addEventListener("click", function () {
        if (!question || !options.input) return;
        options.input.value = question;
        options.input.dispatchEvent(new Event("input", {bubbles: true}));
        options.input.focus();
      });
    }
    list.appendChild(item);
  }

  function makeCard(title, section) {
    var card = document.createElement("article");
    card.className = "personal-briefing-card";
    card.appendChild(createText("h3", "personal-briefing-card-title", title));
    var status = createText("span", "personal-briefing-status",
                            STATUS_LABEL[section.status] || STATUS_LABEL.empty);
    status.dataset.status = section.status || "empty";
    card.appendChild(status);
    return card;
  }

  function render(root, data, options) {
    root.hidden = data.enabled === false;
    if (root.hidden) return;
    var grid = root.querySelector(".personal-briefing-grid");
    if (!grid) {
      grid = document.createElement("div");
      grid.className = "personal-briefing-grid";
      grid.id = "personal-briefing-grid";
      root.appendChild(grid);
    }
    grid.replaceChildren();

    var priority = makeCard("오늘 우선 확인", {status: data.priorities.length ? "ready" : "empty"});
    data.priorities.forEach(function (item) {
      addItem(priority, item.title, item.url, item.follow_up || "", options);
    });
    grid.appendChild(priority);

    var calendar = makeCard("7일 일정", data.calendar);
    data.calendar.items.forEach(function (item) {
      addItem(calendar, item.title, item.url,
              item.title + " 일정 준비사항을 알려줘", options);
    });
    if (data.calendar.truncated) {
      calendar.appendChild(createText("p", "personal-briefing-note", "50건 이상 · Google Calendar에서 전체 보기"));
    }
    grid.appendChild(calendar);

    var mail = makeCard("오늘 메일 · " + data.mail.count_label + " · 안 읽음 " + data.mail.unread, data.mail);
    if (data.mail.summary) mail.appendChild(createText("p", "personal-briefing-summary", data.mail.summary));
    data.mail.items.forEach(function (item) {
      addItem(mail, item.subject, item.url,
              item.from_display + "의 " + item.subject + " 메일을 자세히 요약해줘", options);
    });
    if (data.mail.status === "disconnected") {
      var connect = createText("button", "personal-briefing-connect", "Google 연결");
      connect.type = "button";
      connect.addEventListener("click", options.connect);
      mail.appendChild(connect);
    }
    grid.appendChild(mail);

    var business = makeCard("업무 지표", data.business);
    if (data.business.item) {
      business.appendChild(createText("p", "personal-briefing-date", "기준일 " + data.business.item.for_date));
      business.appendChild(createText("p", "personal-briefing-summary", data.business.item.body));
      addItem(business, data.business.item.title, "", data.business.item.follow_up, options);
    }
    grid.appendChild(business);

    var updated = document.getElementById("personal-briefing-updated");
    if (updated) updated.textContent = data.generated_at ? "갱신 " + data.generated_at : "저장본 없음";
  }

  function create(options) {
    var state = null;
    var root = options.root;

    async function load() {
      renderSkeleton(root);
      try {
        var response = await options.fetchImpl("/api/personal-briefing");
        if (!response.ok) throw new Error("briefing get failed");
        state = await response.json();
      } catch (_e) {
        markRefreshStopped(root);
        return;
      }
      render(root, state, options);
      if (state.enabled && state.needs_refresh) {
        try {
          var fresh = await options.fetchImpl("/api/personal-briefing/refresh", {method: "POST"});
          if (!fresh.ok) throw new Error("briefing refresh failed");
          state = await fresh.json();
          render(root, state, options);
        } catch (_e) {
          markRefreshStopped(root);
        }
      }
    }

    return {load: load, show: function () { if (state) render(root, state, options); },
            refreshAfterConnect: load};
  }

  function markRefreshStopped(root) {
    root.querySelectorAll(".personal-briefing-status").forEach(function (status) {
      if (status.dataset.status === "loading") {
        status.dataset.status = "error";
        status.textContent = "불러오지 못함";
      }
    });
  }

  window.CellaPersonalBriefing = {create: create, safeUrl: safeUrl};
})();
```

Add these functions and call `renderSkeleton(root)` as the first line of `load()` before awaiting GET. In the Calendar
loop, insert a date label whenever `formatKstDate(item.start)` changes from the prior item:

```javascript
  function formatKstDate(value) {
    var date = /^\d{4}-\d{2}-\d{2}$/.test(value) ? new Date(value + "T00:00:00+09:00") : new Date(value);
    return new Intl.DateTimeFormat("ko-KR", {
      timeZone: "Asia/Seoul", month: "numeric", day: "numeric", weekday: "short"
    }).format(date);
  }

  function renderSkeleton(root) {
    root.hidden = false;
    var grid = root.querySelector(".personal-briefing-grid");
    if (!grid) {
      grid = document.createElement("div");
      grid.className = "personal-briefing-grid";
      root.appendChild(grid);
    }
    grid.replaceChildren();
    ["오늘 우선 확인", "7일 일정", "오늘 메일", "업무 지표"].forEach(function (title) {
      var card = makeCard(title, {status: "loading"});
      card.appendChild(createText("div", "personal-briefing-skeleton", ""));
      grid.appendChild(card);
    });
  }
```

Replace the Calendar loop in `render()` with:

```javascript
    var lastDate = "";
    data.calendar.items.forEach(function (item) {
      var dateLabel = formatKstDate(item.start);
      if (dateLabel !== lastDate) {
        calendar.appendChild(createText("h4", "personal-briefing-date", dateLabel));
        lastDate = dateLabel;
      }
      addItem(calendar, item.title, item.url,
              item.title + " 일정 준비사항을 알려줘", options);
      if (item.ended) calendar.lastElementChild.classList.add("ended");
    });
```

- [ ] **Step 6: Add two-column/one-column styles**

```css
.personal-briefing { width: min(960px, 100%); display: flex; flex-direction: column; gap: 12px; }
.personal-briefing-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }
.personal-briefing-card { min-width: 0; border: 1px solid var(--border); border-radius: 16px; background: var(--bg-input); padding: 16px; }
.personal-briefing-item { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.personal-briefing-item.ended { opacity: .55; }
.personal-briefing-status[data-status="error"] { color: var(--error); }
.personal-briefing-skeleton { min-height: 80px; animation: briefing-pulse 1.2s ease-in-out infinite; }
@keyframes briefing-pulse { 0%,100% { opacity:.45; } 50% { opacity:.8; } }
@media (max-width: 768px) {
  .chat-welcome { justify-content: flex-start; padding-top: 24px; }
  .personal-briefing-grid { grid-template-columns: minmax(0, 1fr); }
}
```

Keep existing design tokens; do not introduce fixed light-only colors.

- [ ] **Step 7: Integrate controller without blocking chat startup**

After `/api/auth/me` succeeds:

```javascript
personalBriefingController = window.CellaPersonalBriefing.create({
  root: document.getElementById("personal-briefing"),
  input: chatInput,
  connect: handleGwsConnect,
  fetchImpl: window.fetch.bind(window)
});
personalBriefingController.load();
```

Do not await `load()`. Keep `loadConversations()` and the rest of init moving. In `showWelcome()` call
`personalBriefingController.show()`. When Google polling changes from disconnected to connected, call
`personalBriefingController.refreshAfterConnect()` once. Existing conversation loads continue to set
`chatWelcome.style.display="none"`, so a late refresh cannot overwrite messages.

- [ ] **Step 8: Run browser and source regressions**

Run: `pytest tests/frontend/test_personal_briefing_welcome.py tests/frontend/test_answer_loading_progress.py tests/frontend/test_edit_resend_no_duplicate.py -q`

Expected: all pass at desktop and mobile viewport sizes.

- [ ] **Step 9: Commit the welcome experience**

```bash
git add app/frontend/personal-briefing.js app/frontend/chat.html app/frontend/chat.js app/static/style.css tests/frontend/test_personal_briefing_welcome.py
git commit -m "feat: show personal briefing on the welcome screen"
```

---

### Task 7: 전체 검증, 배포 수집 확인, 프로덕션 배포와 canary

**Files:**
- Verify only: all files from Tasks 1-6
- Verify: `scripts/deploy_new_server.py`
- Verify: `docs/MIGRATION_AI_CRAVER.md`

**Interfaces:**
- Consumes: complete feature and production deployment path.
- Produces: passing full test evidence, deployment evidence, production health/API/UI evidence.

- [ ] **Step 1: Run the complete focused suite**

```powershell
pytest tests/test_google_auth_routes.py tests/test_gws_digest.py tests/test_personal_briefing_store.py tests/test_personal_briefing.py tests/test_personal_briefing_api.py tests/test_gws_gmail.py tests/test_briefing.py tests/test_jwt_secret.py tests/frontend/test_personal_briefing_welcome.py -q
```

Expected: all tests pass with no warning containing mail subject, sender, snippet, calendar title, or token value.

- [ ] **Step 2: Run the repository regression suite**

Run: `pytest -q`

Expected: exit code 0. If an unrelated pre-existing failure appears, record the exact test and prove the focused suite
still passes; do not modify unrelated user work to force green.

- [ ] **Step 3: Run static and diff safety checks**

```powershell
python -c "from app.core.static_checks import ALL; r=[(i,*fn()) for i,fn,_ in ALL]; print(r); raise SystemExit(0 if all(x[1] for x in r) else 1)"
git diff --check
git status --short
```

Expected: static checks pass. `git diff --check` has no whitespace error in Task 1-6 files; pre-existing unrelated
knowledge-map whitespace is reported separately rather than edited.

- [ ] **Step 4: Prove deployment collection contains every runtime file**

```powershell
@'
from scripts.deploy_new_server import PROJ, collect
required = {
    'app/core/google_oauth_state.py',
    'app/core/personal_briefing_store.py',
    'app/core/personal_briefing.py',
    'app/api/personal_briefing_api.py',
    'app/frontend/personal-briefing.js',
    'app/frontend/chat.html',
    'app/frontend/chat.js',
    'app/static/style.css',
}
files = {str(p.relative_to(PROJ)).replace('\\', '/') for p in collect()}
missing = required - files
print({'missing': sorted(missing)})
raise SystemExit(1 if missing else 0)
'@ | python -
```

Expected: the final check prints `{'missing': []}`.

- [ ] **Step 5: Dry-run the real WAS deployment package**

Run: `./sshenv/Scripts/python scripts/deploy_new_server.py was --dry`

Expected: target is `was (10.1.150.5)` and the runtime file count is nonzero. No package/requirements change is
needed because the plan uses installed PyJWT and Google SDKs only.

- [ ] **Step 6: Deploy to the actual production WAS**

Verify `CRAVER_SSH_PW` is present without printing its value, then run:

```powershell
if (-not $env:CRAVER_SSH_PW) { throw 'CRAVER_SSH_PW is not set' }
./sshenv/Scripts/python scripts/deploy_new_server.py was
```

Expected: SFTP transfer completes, `ai-craver` is active, and `/health HTTP 200` is printed. Do not restart or stop
the `172.16.1.250` rollback/CRM host.

- [ ] **Step 7: Verify production security and health without credentials**

```powershell
$health = Invoke-WebRequest -UseBasicParsing http://10.1.100.5/health
$brief = Invoke-WebRequest -UseBasicParsing http://10.1.100.5/api/personal-briefing -SkipHttpErrorCheck
$gws = Invoke-WebRequest -UseBasicParsing http://10.1.100.5/auth/google/status -SkipHttpErrorCheck
@{ health=$health.StatusCode; briefing=$brief.StatusCode; gws=$gws.StatusCode }
```

Expected: `health=200`, `briefing=401`, `gws=401`.

- [ ] **Step 8: Verify authenticated connected and disconnected states in the browser**

Using an existing authorized production browser session:

1. Open `http://10.1.100.5/` and confirm the welcome page renders before Google refresh finishes.
2. For a Google-connected account, confirm today mail, today+6-day calendar, last-updated status, and links.
3. Open an existing conversation during refresh and confirm messages are not replaced.
4. Return Home and confirm the refreshed in-memory cards appear.
5. Disconnect Google and confirm token/snapshot removal produces the connect CTA without exposing prior content.
6. At 390px viewport confirm one-column cards and hover/focus full titles.

No mail is marked read and no calendar event is created or changed during this check.

- [ ] **Step 9: Inspect production logs for content-free observability**

Run through the deployment SSH helper or an approved SSH session:

```bash
journalctl -u ai-craver --since '-10min' --no-pager | grep -E 'personal_briefing|oauth_state|ERROR|Traceback'
```

Expected: only user id/count/duration/status fields; no subject, sender, snippet, event title, access token, refresh token,
or raw Google exception body.

- [ ] **Step 10: Record final deployment commit**

If verification required no code changes, do not create an empty commit. If a canary fix was necessary, rerun Steps 1-9,
stage only files from this plan, and commit with:

```bash
git commit -m "fix: harden personal briefing production canary"
```

Report the deployed commit hash, focused/full test counts, production health codes, and whether authenticated visual
verification covered both Google-connected and disconnected states.
