import time
from datetime import date, datetime
from pathlib import Path

import pytest
from fastapi import HTTPException, Request, Response

from app.api import admin_api, auth_api, auth_middleware
from app.db import mariadb
from app.db.models import User


def test_visitor_period_keys_fill_days_weeks_and_months():
    assert admin_api._visitor_period_keys(date(2026, 8, 1), date(2026, 8, 3), "day") == [
        "2026-08-01", "2026-08-02", "2026-08-03"
    ]


def test_visitor_analytics_requires_admin():
    with pytest.raises(HTTPException) as exc:
        admin_api._require_admin(User(id=2, role="user"))
    assert exc.value.status_code == 403
    assert admin_api._visitor_period_keys(date(2026, 8, 5), date(2026, 8, 18), "week") == [
        "2026-08-03", "2026-08-10", "2026-08-17"
    ]
    assert admin_api._visitor_period_keys(date(2025, 11, 20), date(2026, 2, 2), "month") == [
        "2025-11-01", "2025-12-01", "2026-01-01", "2026-02-01"
    ]


def test_admin_granted_user_can_access_visitor_analytics_outside_the_original_department():
    """Changing access back to an AD-department rule must break this manual grant."""
    guard = getattr(admin_api, "_require_visitor_analytics_access", None)
    assert guard is not None, "visitor analytics needs its own scoped access guard"
    user = User(id=52, role="user", department="운영본부 > 재무팀")
    setattr(user, "can_view_visitor_analytics", True)
    assert guard(user) is user


def test_data_business_team_member_without_manual_grant_cannot_access_visitor_analytics():
    """The old department-wide automatic grant must not survive the manual control."""
    guard = getattr(admin_api, "_require_visitor_analytics_access", None)
    assert guard is not None, "visitor analytics needs its own scoped access guard"
    with pytest.raises(HTTPException) as exc:
        guard(User(id=53, role="user", department="운영본부 > 데이터 비즈니스팀"))
    assert exc.value.status_code == 403


def test_visitor_endpoint_uses_scoped_guard_without_weakening_other_admin_routes():
    guard = getattr(admin_api, "_require_visitor_analytics_access", None)
    assert guard is not None
    route = next(route for route in admin_api.admin_router.routes if route.path == "/api/admin/visitor-analytics")
    assert [dependency.call for dependency in route.dependant.dependencies] == [guard]
    with pytest.raises(HTTPException):
        admin_api._require_admin(User(id=52, role="user", department="데이터 비즈니스팀"))


@pytest.mark.asyncio
@pytest.mark.parametrize(("department", "granted"), [
    ("운영본부 > 재무팀", True),
    ("운영본부 > 데이터 비즈니스팀 > 데이터분석파트", False),
])
async def test_me_exposes_manually_granted_visitor_analytics_capability(monkeypatch, department, granted):
    async def noop(_user_id):
        return None

    user = User(id=74, role="user", department=department)
    setattr(user, "can_view_visitor_analytics", granted)
    monkeypatch.setattr(auth_api, "_record_authenticated_visit", noop)
    auth_api._me_last_refresh[user.id] = time.time()
    try:
        result = await auth_api.me(
            Response(), user,
            request=Request({"type": "http", "method": "GET", "path": "/api/auth/me", "headers": []}),
        )
    finally:
        auth_api._me_last_refresh.pop(user.id, None)
    assert result["can_view_visitor_analytics"] is granted


@pytest.mark.asyncio
async def test_authenticated_user_loads_manual_visitor_permission_from_ad_row(monkeypatch):
    """Omitting the DB column from the authenticated-user query must deny a real grant."""
    row = {
        "id": 74,
        "email": "finance@example.com",
        "display_name": "Finance User",
        "role": "user",
        "allowed_models": None,
        "ad_user_id": 17,
        "ad_name": "Finance User",
        "ad_email": "finance@example.com",
        "department": "운영본부 > 재무팀",
        "must_change_password": 0,
        "can_view_visitor_analytics": 1,
    }
    request = type("Request", (), {
        "state": type("State", (), {})(),
        "url": type("Url", (), {"path": "/api/admin/visitor-analytics"})(),
    })()
    monkeypatch.setattr(auth_middleware, "_extract_user_id", lambda _request: 74)
    monkeypatch.setattr(auth_middleware, "fetch_one", lambda *_args, **_kwargs: row)
    auth_middleware._user_cache.clear()
    try:
        user = await auth_middleware.get_current_user(request)
    finally:
        auth_middleware._user_cache.clear()
    assert getattr(user, "can_view_visitor_analytics", False) is True


def test_admin_keeps_visitor_analytics_access_independent_of_department():
    guard = admin_api._require_visitor_analytics_access
    user = User(id=1, role="admin", department="")
    assert guard(user) is user


@pytest.mark.asyncio
async def test_visitor_analytics_returns_summary_series_and_people(monkeypatch):
    monkeypatch.setattr(admin_api, "_VISITOR_TRACKING_STARTED_ON", date.today())

    async def fake_fetch(sql, params=()):
        if "current_unique" in sql:
            return [{"current_unique": 12, "previous_unique": 8, "current_visits": 31, "today_unique": 4}]
        if "AS bucket" in sql:
            today = date.today().isoformat()
            return [{"bucket": today, "visitors": 4, "visits": 7}]
        if "MAX(v.last_seen_at)" in sql:
            return [{
                "id": 7,
                "name": "김테스트",
                "email": "test@example.com",
                "department": "운영본부",
                "last_seen_at": datetime(2026, 8, 11, 9, 30),
                "active_days": 3,
                "visits": 5,
            }]
        if "COUNT(*) AS cnt FROM users" in sql:
            return [{"cnt": 25}]
        raise AssertionError(sql)

    monkeypatch.setattr(admin_api, "_db_fetch_all", fake_fetch)
    result = await admin_api.get_visitor_analytics(days=30, _=User(id=1, role="admin"))

    assert result["summary"] == {
        "unique_visitors": 12,
        "previous_unique_visitors": None,
        "change_pct": None,
        "today_visitors": 4,
        "page_visits": 31,
        "registered_users": 25,
    }
    assert len(result["series"]) == 1
    assert result["series"][-1]["visitors"] == 4
    assert result["visitors"][0]["name"] == "김테스트"
    assert result["visitors"][0]["last_seen_at"] == "2026-08-11T09:30:00"
    assert result["tracking_started_at"] == date.today().isoformat()
    assert result["range"]["is_partial"] is True
    assert result["availability"] == {
        "tracked_days": 1,
        "available_ranges": [30],
        "comparison_ready": False,
        "comparison_requires_days": 60,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(("sort_key", "expected_order"), [
    ("recent", "ORDER BY last_seen_at DESC, visits DESC, u.id ASC"),
    ("active_days", "ORDER BY active_days DESC, last_seen_at DESC, u.id ASC"),
    ("visits", "ORDER BY visits DESC, last_seen_at DESC, u.id ASC"),
])
async def test_visitor_analytics_applies_requested_descending_ranking(
    monkeypatch, sort_key, expected_order,
):
    """A changed or ignored sort key must not return the wrong top 50 visitors."""
    monkeypatch.setattr(admin_api, "_VISITOR_TRACKING_STARTED_ON", date.today())
    visitor_queries = []

    async def fake_fetch(sql, params=()):
        if "current_unique" in sql:
            return [{}]
        if "AS bucket" in sql:
            return []
        if "MAX(v.last_seen_at)" in sql:
            visitor_queries.append(sql)
            return []
        if "COUNT(*) AS cnt FROM users" in sql:
            return [{"cnt": 0}]
        raise AssertionError(sql)

    monkeypatch.setattr(admin_api, "_db_fetch_all", fake_fetch)
    result = await admin_api.get_visitor_analytics(
        days=30,
        sort=sort_key,
        _=User(id=1, role="admin"),
    )

    assert len(visitor_queries) == 1
    assert expected_order in visitor_queries[0]
    assert result["sort"] == sort_key


@pytest.mark.asyncio
async def test_visitor_analytics_rejects_unknown_sort_key():
    with pytest.raises(HTTPException) as exc:
        await admin_api.get_visitor_analytics(
            days=30,
            sort="email",
            _=User(id=1, role="admin"),
        )
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_visitor_analytics_rejects_unsupported_range():
    with pytest.raises(HTTPException) as exc:
        await admin_api.get_visitor_analytics(days=14, _=User(id=1, role="admin"))
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_visit_record_is_upserted_without_blocking_auth(monkeypatch):
    calls = []

    async def capture(sql, params=()):
        calls.append((sql, params))
        return 1

    monkeypatch.setattr(auth_api, "_db_execute", capture)
    await auth_api._record_authenticated_visit(42)
    assert calls[0][1] == (42,)
    assert "ON DUPLICATE KEY UPDATE" in calls[0][0]


def test_visit_table_has_daily_uniqueness(monkeypatch):
    statements = []
    monkeypatch.setattr(mariadb, "execute", lambda sql, params=(): statements.append(sql) or 0)
    mariadb.ensure_user_visits_table()
    assert "UNIQUE KEY uq_user_visits_user_date" in statements[0]
    assert "INDEX idx_user_visits_date" in statements[0]


def test_visitor_analytics_lives_in_admin_not_system_status():
    project_root = Path(__file__).resolve().parents[1]
    html = (project_root / "app/frontend/chat.html").read_text(encoding="utf-8")
    script = (project_root / "app/frontend/chat.js").read_text(encoding="utf-8")

    status_markup = html[html.index('id="skin-status-drawer"'):html.index('<!-- Wiki Drawer -->')]
    admin_markup = html[html.index('id="skin-admin-drawer"'):html.index('<!-- Theme Toggle -->')]
    assert 'id="visitor-analytics"' not in status_markup
    assert 'data-tab="visitors"' in admin_markup
    assert 'id="tab-visitors"' in admin_markup
    assert 'id="visitor-analytics"' in admin_markup
    assert 'if (tab.dataset.tab === "visitors") loadVisitorAnalytics' in script
