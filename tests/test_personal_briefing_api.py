import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import auth_routes
from app.api import personal_briefing_api as api
from app.api.auth_middleware import get_current_user
from app.api.personal_briefing_api import router
from app.core import personal_briefing as pb
from app.db.models import User


OWNER = User(
    id=7,
    email="owner@example.com",
    name="Owner",
    department="D",
    role="user",
    allowed_models="skin1004-Analysis",
)


def _app_for_owner() -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: OWNER
    return app


def test_get_uses_dependency_user_only(monkeypatch):
    seen = {}

    def fake_cached(user):
        seen["user_id"] = user.id
        return {"enabled": True}

    monkeypatch.setattr("app.api.personal_briefing_api.get_cached_for_user", fake_cached)
    response = TestClient(_app_for_owner()).get(
        "/api/personal-briefing?user_id=8&user_email=other@example.com"
    )

    assert response.status_code == 200
    assert seen["user_id"] == 7


def test_refresh_requires_auth():
    app = FastAPI()
    app.include_router(router)

    assert TestClient(app).get("/api/personal-briefing").status_code == 401
    assert TestClient(app).post("/api/personal-briefing/refresh").status_code == 401


def test_disabled_flag_short_circuits_core(monkeypatch):
    monkeypatch.setattr(
        "app.api.personal_briefing_api.get_settings",
        lambda: type("Settings", (), {"personal_briefing_enabled": False})(),
    )
    monkeypatch.setattr(
        "app.api.personal_briefing_api.get_cached_for_user",
        lambda _user: (_ for _ in ()).throw(AssertionError("core called")),
    )
    client = TestClient(_app_for_owner())

    assert client.get("/api/personal-briefing").json() == {"enabled": False}
    assert client.post("/api/personal-briefing/refresh").status_code == 404


def test_refresh_timeout_returns_same_day_last_known_good_cache(monkeypatch):
    timeouts = []

    async def timeout(awaitable, timeout):
        awaitable.cancel()
        timeouts.append(timeout)
        raise asyncio.TimeoutError()

    async def harmless_refresh(_user, *, now):
        await asyncio.sleep(0)
        return {"for_date": str(now.date())}

    monkeypatch.setattr(api.asyncio, "wait_for", timeout)
    monkeypatch.setattr(api, "refresh_for_user", harmless_refresh)
    cached = {
        "enabled": True, "for_date": "2026-08-25", "timezone": "Asia/Seoul",
        "generated_at": "2026-08-25T08:30:00+09:00", "needs_refresh": True,
        "google": {"connected": True, "account": "connected@example.com"},
        "priorities": [{"source": "mail", "source_id": "m1", "title": "saved"}],
        "calendar": {"status": "ready", "items": [{"id": "e1"}], "error_code": ""},
        "mail": {"status": "ready", "items": [{"id": "m1"}], "error_code": ""},
        "business": {"status": "ready", "item": {"id": "b1"}},
    }
    monkeypatch.setattr(api, "get_cached_for_user", lambda *_args: cached)

    response = TestClient(_app_for_owner()).post("/api/personal-briefing/refresh")

    assert response.status_code == 200
    assert response.json()["needs_refresh"] is False
    assert response.json()["for_date"] == "2026-08-25"
    assert response.json()["calendar"]["items"] == [{"id": "e1"}]
    assert response.json()["mail"]["items"] == [{"id": "m1"}]
    assert response.json()["priorities"][0]["source_id"] == "m1"
    assert response.json()["calendar"]["status"] == "stale"
    assert response.json()["mail"]["status"] == "stale"
    assert response.json()["calendar"]["error_code"] == "google_timeout"
    assert response.json()["mail"]["error_code"] == "google_timeout"
    assert len(timeouts) == 1
    assert 0 < timeouts[0] <= 15.0


@pytest.mark.asyncio
async def test_timed_out_refresh_keeps_running_under_shared_lock(monkeypatch):
    """The HTTP budget must not cancel a thread-backed refresh and release its lock early."""
    release = asyncio.Event()
    finished = asyncio.Event()
    cached = {
        "enabled": True, "for_date": "2026-08-25", "needs_refresh": True,
        "calendar": {"status": "empty", "items": []},
        "mail": {"status": "empty", "items": []},
    }

    async def stalled(_user, *, now):
        try:
            await release.wait()
            return cached
        finally:
            finished.set()

    monkeypatch.setattr(api, "get_settings", lambda: type("S", (), {"personal_briefing_enabled": True})())
    monkeypatch.setattr(api, "get_cached_for_user", lambda *_args: cached)
    monkeypatch.setattr(api, "refresh_for_user", stalled)
    monkeypatch.setattr(api, "_REFRESH_BUDGET_SECONDS", 0.01)

    result = await api.refresh_personal_briefing(OWNER)

    assert result["for_date"] == "2026-08-25"
    assert not finished.is_set()
    release.set()
    await asyncio.wait_for(finished.wait(), timeout=1)


def test_refresh_pins_one_kst_now_across_midnight_fallback(monkeypatch):
    seoul = ZoneInfo("Asia/Seoul")
    before_midnight = datetime(2026, 8, 25, 23, 59, 59, tzinfo=seoul)
    after_midnight = datetime(2026, 8, 26, 0, 0, 1, tzinfo=seoul)
    seen = {}

    class CrossingClock:
        calls = 0

        @classmethod
        def now(cls, _tz):
            cls.calls += 1
            return before_midnight if cls.calls == 1 else after_midnight

    async def timed_out_refresh(_user, *, now):
        seen["refresh_now"] = now
        raise asyncio.TimeoutError()

    def cached(_user, now):
        seen["cached_now"] = now
        return {
            "enabled": True,
            "for_date": str(now.date()),
            "needs_refresh": True,
            "calendar": {"status": "empty", "items": [], "error_code": ""},
            "mail": {"status": "empty", "items": [], "error_code": ""},
        }

    monkeypatch.setattr(api, "datetime", CrossingClock)
    monkeypatch.setattr(api, "refresh_for_user", timed_out_refresh)
    monkeypatch.setattr(api, "get_cached_for_user", cached)

    response = TestClient(_app_for_owner()).post("/api/personal-briefing/refresh")

    assert response.status_code == 200
    assert CrossingClock.calls == 1
    assert seen == {"refresh_now": before_midnight, "cached_now": before_midnight}
    assert response.json()["for_date"] == "2026-08-25"


def test_personal_briefing_job_is_monitored():
    from app.core.self_check import EXPECTED_JOBS

    assert "personal_briefing_daily" in EXPECTED_JOBS


def test_main_registers_personal_briefing_router_and_schedule():
    import inspect

    from app import main

    src = inspect.getsource(main.create_app)
    assert "personal_briefing_router" in src
    assert 'id="personal_briefing_daily"' in src


def test_oauth_revoke_deletes_owner_snapshot_even_without_token(monkeypatch):
    deleted = {}

    class MissingTokenManager:
        def revoke_credentials(self, email):
            deleted["email"] = email
            return False

    app = FastAPI()
    app.include_router(auth_routes.auth_router)
    app.dependency_overrides[get_current_user] = lambda: OWNER
    monkeypatch.setattr(auth_routes, "_get_auth_manager", lambda: MissingTokenManager())
    monkeypatch.setattr(auth_routes, "delete_for_user", lambda user_id: deleted.update(user_id=user_id))

    response = TestClient(app).post("/auth/google/revoke")

    assert response.status_code == 200
    assert deleted == {"email": "owner@example.com", "user_id": 7}


@pytest.mark.asyncio
async def test_oauth_revoke_waits_for_the_shared_user_refresh_lock(monkeypatch):
    """Revoke cannot delete credentials while the same user's refresh is in flight."""
    calls = []

    class Manager:
        def revoke_credentials(self, email):
            calls.append(("revoke", email))
            return True

    monkeypatch.setattr(auth_routes, "_get_auth_manager", lambda: Manager())
    monkeypatch.setattr(auth_routes, "delete_for_user", lambda user_id: calls.append(("delete", user_id)))
    lock = pb.get_user_refresh_lock(OWNER.id)
    await lock.acquire()
    try:
        task = asyncio.create_task(auth_routes.google_revoke(OWNER))
        await asyncio.sleep(0)
        assert calls == []
    finally:
        lock.release()

    result = await task
    assert result["revoked"] is True
    assert calls == [("revoke", "owner@example.com"), ("delete", 7)]
