"""New Entra users need an assigned data group on HTTP and scheduled paths."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import sqlite3
from types import SimpleNamespace

import jwt
import pytest
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from app.api import admin_group_api, auth_middleware, reports_api, routes, saved_questions_api, sql_export_api
from app.core import saved_questions
from app.db.models import User


SECRET = "test-only-group-assignment-jwt-" + "x" * 40
TENANT = "11111111-1111-4111-8111-111111111111"
OID = "22222222-2222-4222-8222-222222222222"
ADMIN = User(id=1, email="admin@example.com", role="admin")
CHAT = {"messages": [{"role": "user", "content": "이번 달 매출"}]}


class _Store:
    """Execute the actual permission/membership SQL in an isolated SQLite DB."""

    def __init__(self, path):
        self.path = str(path)
        connection = sqlite3.connect(self.path)
        connection.executescript("""
            CREATE TABLE directory_users (
                id INTEGER PRIMARY KEY, username TEXT, display_name TEXT, email TEXT,
                department TEXT, is_active INTEGER, can_view_fi INTEGER,
                can_view_visitor_analytics INTEGER DEFAULT 0
            );
            CREATE TABLE users (
                id INTEGER PRIMARY KEY, email TEXT, display_name TEXT, role TEXT,
                allowed_models TEXT, ad_user_id INTEGER, is_active INTEGER,
                must_change_password INTEGER DEFAULT 0, requires_group_assignment INTEGER
            );
            CREATE TABLE access_groups (
                id INTEGER PRIMARY KEY, name TEXT, description TEXT, brand_filter TEXT
            );
            CREATE TABLE user_groups (
                id INTEGER PRIMARY KEY AUTOINCREMENT, ad_user_id INTEGER, group_id INTEGER,
                UNIQUE(ad_user_id, group_id)
            );
            INSERT INTO directory_users VALUES
                (41, 'employee', 'Employee', 'employee@cravercorp.com', 'Team', 1, 0, 0);
            INSERT INTO users VALUES
                (7, 'employee@cravercorp.com', 'Employee', 'user', 'model', 41, 1, 0, 1);
            INSERT INTO access_groups VALUES (10, 'SK', '', 'SK,CBT');
        """)
        connection.close()
        self.reads = 0

    def _run(self, sql, params, *, read=False, all_rows=False):
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        try:
            cursor = connection.execute(sql.replace("%s", "?"), params)
            if read:
                self.reads += 1
                if all_rows:
                    return [dict(row) for row in cursor.fetchall()]
                row = cursor.fetchone()
                return dict(row) if row is not None else None
            connection.commit()
            return cursor.rowcount
        finally:
            connection.close()

    def fetch_one(self, sql, params=()):
        return self._run(sql, params, read=True)

    def fetch_all(self, sql, params=()):
        return self._run(sql, params, read=True, all_rows=True)

    def execute(self, sql, params=()):
        return self._run(sql, params)

    def assign(self, group_id=10):
        self.execute("INSERT INTO user_groups(ad_user_id,group_id) VALUES (41,%s)", (group_id,))


@pytest.fixture
def store(tmp_path, monkeypatch):
    state = _Store(tmp_path / "groups.sqlite")
    monkeypatch.setattr(auth_middleware, "_user_cache", {})
    monkeypatch.setattr(auth_middleware, "get_settings", lambda: SimpleNamespace(
        jwt_secret_key=SECRET, password_login_enabled=False, entra_tenant_id=TENANT,
    ))
    for module in (auth_middleware, routes, saved_questions, admin_group_api):
        monkeypatch.setattr(module, "fetch_one", state.fetch_one)
    monkeypatch.setattr(admin_group_api, "fetch_all", state.fetch_all)
    monkeypatch.setattr(admin_group_api, "execute", state.execute)
    return state


def _session_token(**token_claims):
    return jwt.encode({
        "user_id": 7, "exp": datetime.now(timezone.utc) + timedelta(hours=1),
        "purpose": "session", "auth_provider": "entra", "entra_tid": TENANT,
        "entra_oid": OID, **token_claims,
    }, SECRET, algorithm="HS256")


def _request(path="/api/reports", **token_claims):
    token = _session_token(**token_claims)
    return Request({"type": "http", "method": "GET", "scheme": "http", "path": path,
                    "headers": [(b"cookie", f"token={token}".encode())]})


@pytest.fixture
def client(store, monkeypatch):
    observed = []

    class Orchestrator:
        async def route_and_execute(self, query, messages, model, **kwargs):
            observed.append(kwargs)
            return {"answer": "authorized answer", "source": "bigquery"}

        async def route_and_stream(self, *args, **kwargs):
            pytest.fail("A pending user's stream reached the orchestrator")
            yield "done", "unreachable"

    monkeypatch.setattr(routes, "_get_orchestrator", lambda: Orchestrator())
    app = FastAPI()
    for router in (routes.router, reports_api.router, saved_questions_api.router, sql_export_api.router):
        app.include_router(router)

    async def identity(user: User = Depends(auth_middleware.get_current_user)):
        return {"id": user.id, "requires_group_assignment": user.requires_group_assignment}

    for path in ("/api/auth/me", "/api/auth/logout", "/api/profile", "/api/personal-briefing"):
        app.add_api_route(path, identity, methods=["GET"])
    session = TestClient(app, raise_server_exceptions=False)
    session.cookies.set("token", _session_token(role="admin", brand_filter="UM"))
    session.observed = observed
    return session


@pytest.mark.parametrize("stream", [False, True])
def test_pending_chat_is_denied_before_generation_even_with_admin_jwt_claim(client, stream):
    response = client.post("/v1/chat/completions", json={**CHAT, "stream": stream})
    assert response.status_code == 403
    assert response.json()["detail"] == auth_middleware.GROUP_ASSIGNMENT_MESSAGE
    assert "text/event-stream" not in response.headers.get("content-type", "")
    assert client.observed == []


@pytest.mark.parametrize("method,path,payload", [
    ("GET", "/api/reports", None), ("GET", "/api/reports/specs", None),
    ("POST", "/api/reports", {"question": "이번 달 매출 보고서"}),
    ("GET", "/api/reports/1/shares", None),
    ("GET", "/api/sql-results/private-token/csv", None),
    ("GET", "/api/saved-questions", None),
    ("POST", "/api/saved-questions", {"question": "매출", "cadence": "daily"}),
    ("DELETE", "/api/saved-questions/9", None),
])
def test_pending_user_cannot_bypass_chat_using_data_endpoints(client, method, path, payload):
    response = client.request(method, path, json=payload)
    assert response.status_code == 403, response.text
    assert response.json()["detail"] == auth_middleware.GROUP_ASSIGNMENT_MESSAGE


@pytest.mark.parametrize("path", ["/api/auth/me", "/api/auth/logout", "/api/profile", "/api/personal-briefing"])
def test_pending_user_can_still_access_identity_and_personal_features(client, path):
    response = client.get(path)
    assert response.status_code == 200 and response.json()["requires_group_assignment"] is True


@pytest.mark.parametrize("case,brand,fi", [("assigned", "SK,CBT", False), ("legacy", None, False),
                                         ("admin", None, True)])
def test_assigned_legacy_and_admin_accounts_keep_their_data_access(client, store, case, brand, fi):
    if case == "assigned":
        store.assign()
    elif case == "legacy":
        store.execute("UPDATE users SET requires_group_assignment=0 WHERE id=7")
    else:
        store.execute("UPDATE users SET role='admin' WHERE id=7")
    response = client.post("/v1/chat/completions", json=CHAT)
    assert response.status_code == 200, response.text
    assert response.json()["choices"][0]["message"]["content"] == "authorized answer"
    assert client.observed[0]["brand_filter"] == brand
    assert client.observed[0]["can_view_fi"] is fi and client.observed[0]["user_id"] == 7


@pytest.mark.parametrize("table", ["users", "directory_users"])
def test_inactive_account_or_employee_is_blocked_even_on_profile(client, store, table):
    store.execute(f"UPDATE {table} SET is_active=0")
    response = client.get("/api/profile")
    assert response.status_code == 403 and "중지된 계정" in response.text


@pytest.mark.parametrize("brand_filter", [None, ""])
def test_membership_without_a_brand_filter_does_not_unblock_new_user(client, store, brand_filter):
    store.execute("UPDATE access_groups SET brand_filter=%s WHERE id=10", (brand_filter,))
    store.assign()
    assert client.post("/v1/chat/completions", json=CHAT).status_code == 403
    assert client.observed == []


def test_empty_group_before_real_group_cannot_remove_assigned_brand_restriction(client, store):
    store.execute("INSERT INTO access_groups VALUES (9,'No brand','',NULL)")
    store.assign(9)
    store.assign(10)
    response = client.post("/v1/chat/completions", json=CHAT)
    assert response.status_code == 200
    assert client.observed[0]["brand_filter"] == "SK,CBT"


@pytest.mark.parametrize("missing", [False, True])
def test_failed_or_missing_live_rights_lookup_cannot_become_unrestricted(client, store, monkeypatch, missing):
    store.assign()
    assert client.get("/api/profile").status_code == 200  # Warm the identity cache.

    def fail(sql, params=()):
        if missing:
            return None
        raise RuntimeError("private storage failure")

    monkeypatch.setattr(routes, "fetch_one", fail)
    response = client.post("/v1/chat/completions", json=CHAT)
    assert response.status_code == 503
    assert "private storage failure" not in response.text and client.observed == []


def test_group_removed_by_another_worker_cannot_open_all_brands(client, store):
    store.assign()
    assert client.get("/api/profile").status_code == 200
    assert not auth_middleware._user_cache[7][0].requires_group_assignment
    # Simulate a write in another process without this worker's cache eviction.
    store.execute("DELETE FROM user_groups")
    response = client.post("/v1/chat/completions", json=CHAT)
    assert response.status_code == 403
    assert client.observed == []


@pytest.mark.asyncio
async def test_assignment_refreshes_cached_pending_identity_immediately(store):
    pending = await auth_middleware.get_current_user(_request("/api/profile"))
    assert pending.requires_group_assignment is True
    assert 7 in auth_middleware._user_cache

    result = await admin_group_api.assign_users_to_group(10, admin_group_api.AssignUsers(ad_user_ids=[41]), ADMIN)

    assert result["added"] == 1
    allowed = await auth_middleware.get_current_user(_request())
    assert allowed.id == 7 and allowed.requires_group_assignment is False
    assert store.fetch_one("SELECT requires_group_assignment FROM users WHERE id=7")["requires_group_assignment"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["remove", "delete", "clear_brand"])
async def test_group_revoke_delete_or_brand_clear_closes_cached_data_access_immediately(store, change):
    store.assign()
    assert not (await auth_middleware.get_current_user(_request())).requires_group_assignment
    if change == "remove":
        await admin_group_api.remove_users_from_group(10, admin_group_api.RemoveUsers(ad_user_ids=[41]), ADMIN)
    elif change == "delete":
        await admin_group_api.delete_group(10, ADMIN)
    else:
        await admin_group_api.update_group(10, admin_group_api.GroupUpdate(brand_filter=""), ADMIN)

    with pytest.raises(HTTPException) as error:
        await auth_middleware.get_current_user(_request())
    assert error.value.status_code == 403 and error.value.detail == auth_middleware.GROUP_ASSIGNMENT_MESSAGE


@pytest.mark.asyncio
@pytest.mark.parametrize("case,allowed,brand,fi", [
    ("pending", False, None, False), ("assigned", True, "SK,CBT", True),
    ("legacy", True, None, False), ("admin", True, None, True),
    ("inactive", False, None, False),
])
async def test_scheduled_questions_use_current_group_and_user_permissions(store, monkeypatch, case, allowed, brand, fi):
    if case == "assigned":
        store.assign()
        store.execute("UPDATE directory_users SET can_view_fi=1 WHERE id=41")
    elif case == "legacy":
        store.execute("UPDATE users SET requires_group_assignment=0 WHERE id=7")
    elif case == "admin":
        store.execute("UPDATE users SET role='admin' WHERE id=7")
    elif case == "inactive":
        store.execute("UPDATE directory_users SET is_active=0 WHERE id=41")
    monkeypatch.setattr(saved_questions, "due", lambda today: [{"id": 5, "user_id": 7, "question": "오늘 매출"}])
    observed, recorded = [], []

    class Orchestrator:
        async def route_and_execute(self, query, messages, model, **kwargs):
            observed.append(kwargs)
            return {"answer": "authorized scheduled answer"}

    monkeypatch.setattr(saved_questions, "_new_orchestrator", lambda: Orchestrator())
    monkeypatch.setattr(saved_questions, "record_result", lambda qid, **kwargs: recorded.append((qid, kwargs)))
    result = await saved_questions.run_saved_questions(datetime(2026, 9, 8, 9, 0))
    assert result["selected"] == 1
    assert bool(observed) is allowed
    if allowed:
        assert result["succeeded"] == 1 and result["failed"] == 0
        assert observed[0]["brand_filter"] == brand and observed[0]["user_id"] == 7
        assert observed[0]["can_view_fi"] is fi
        assert observed[0]["user_email"] == "employee@cravercorp.com"
        assert recorded == [(5, {"answer": "authorized scheduled answer"})]
    else:
        assert result["failed"] == 1 and recorded[0][1].get("error")
