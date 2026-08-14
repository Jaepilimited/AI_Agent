from datetime import date, datetime
from pathlib import Path

import pytest
from fastapi import HTTPException

from app.api import admin_api, auth_api
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
