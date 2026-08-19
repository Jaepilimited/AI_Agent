"""보고서 공유 알림 — 2026-08-19.

메일로 보내려 했으나 **서버에서 메일이 나가지 않는다** (WAS·APP 양쪽 SMTP 차단,
로컬 MTA 없음, Google OAuth 는 gmail.readonly). IT 가 릴레이를 열기 전까지 앱 안에서
알린다. 열리면 같은 문구를 메일로도 보내도록 확장한다.

알림은 **별도 테이블이 아니라 파생값**이다 — "나에게 공유됐는데 아직 안 본 것".
그래서 공유를 해제하면 알림도 함께 사라진다. 사본을 만들면 동기화가 어긋난다.
"""

from __future__ import annotations

import pytest

from app.reports import store


class _FakeDB:
    """report_shares 를 흉내내는 최소 저장소 — SQL 대신 호출 인자를 검사한다."""

    def __init__(self):
        self.sql: list[tuple] = []
        self.unseen = 2

    def fetch_one(self, sql, params=None):
        self.sql.append((sql, params))
        return {"c": self.unseen}

    def fetch_all(self, sql, params=None):
        self.sql.append((sql, params))
        return []

    def execute(self, sql, params=None):
        self.sql.append((sql, params))
        return 1


@pytest.fixture
def db(monkeypatch):
    f = _FakeDB()
    monkeypatch.setattr(store, "fetch_one", f.fetch_one)
    monkeypatch.setattr(store, "fetch_all", f.fetch_all)
    monkeypatch.setattr(store, "execute", f.execute)
    return f


def test_unseen_count_scoped_to_the_user(db):
    store.unseen_count(7)
    sql, params = db.sql[-1]
    assert "s.user_id = %s" in sql and "seen_at IS NULL" in sql
    assert params == (7,)


def test_notifications_are_scoped_to_the_user(db):
    """⛔ 남의 알림이 보이면 보고서 제목·질문이 그대로 새어 나간다."""
    store.list_notifications(7)
    sql, params = db.sql[-1]
    assert "WHERE s.user_id = %s" in sql
    assert params[0] == 7


def test_mark_seen_only_touches_that_users_row(db):
    store.mark_seen(11, 7)
    sql, params = db.sql[-1]
    assert "UPDATE report_shares" in sql
    assert "report_id = %s AND user_id = %s" in sql
    assert "seen_at IS NULL" in sql      # 이미 읽은 것의 시각을 덮어쓰지 않는다
    assert params == (11, 7)


def test_mark_all_seen_scoped(db):
    store.mark_all_seen(7)
    sql, params = db.sql[-1]
    assert "user_id = %s" in sql and "seen_at IS NULL" in sql
    assert params == (7,)


def test_seen_column_is_added_idempotently(monkeypatch):
    """이미 붙어 있으면 ALTER 를 다시 던지지 않는다 (앱 기동마다 도는 코드다)."""
    calls: list[str] = []
    monkeypatch.setattr(store, "fetch_all", lambda *a, **k: [{"Field": "seen_at"}])
    monkeypatch.setattr(store, "execute", lambda sql, *a, **k: calls.append(sql))
    store.ensure_seen_column()
    assert not [c for c in calls if "ALTER" in c]

    monkeypatch.setattr(store, "fetch_all", lambda *a, **k: [])
    store.ensure_seen_column()
    assert any("ALTER TABLE report_shares" in c for c in calls)


def test_opening_a_shared_report_marks_it_read():
    """읽음 처리는 **보고서를 여는 한 곳**에서만 한다.

    목록에서 눌렀든 채팅 링크로 왔든 주소를 직접 쳤든 같은 자리를 지난다 —
    여러 곳에 흩으면 한 경로에서만 배지가 안 사라진다.
    """
    import inspect

    from app.api import reports_api

    src = inspect.getsource(reports_api.read_report)
    assert "mark_seen" in src
    assert "is_owner" in src        # 내가 만든 보고서에는 알림이 없다


def test_notifications_api_is_not_admin_only():
    """모든 사용자에게 적용된다 — 관리자 전용 의존성이 붙어 있으면 안 된다."""
    import inspect

    from app.api import notifications_api

    src = inspect.getsource(notifications_api)
    assert "get_current_user" in src
    assert "require_admin" not in src
    assert "user.id" in src         # 대상은 서버가 JWT 에서 정한다
