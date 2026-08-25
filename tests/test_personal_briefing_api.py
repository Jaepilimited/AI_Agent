import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import auth_routes
from app.api import personal_briefing_api as api
from app.api.auth_middleware import get_current_user
from app.api.personal_briefing_api import router
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


def test_refresh_timeout_bounds_stalled_cache_fallback(monkeypatch):
    timeouts = []

    async def timeout(awaitable, timeout):
        awaitable.close()
        timeouts.append(timeout)
        raise asyncio.TimeoutError()

    remaining = iter((15.0, 0.05))
    monkeypatch.setattr(api, "_remaining_seconds", lambda _deadline: next(remaining))
    monkeypatch.setattr(api.asyncio, "wait_for", timeout)
    monkeypatch.setattr(
        api, "get_cached_for_user", lambda *_args: (_ for _ in ()).throw(AssertionError("cache ran")),
    )

    response = TestClient(_app_for_owner()).post("/api/personal-briefing/refresh")

    assert response.status_code == 200
    assert response.json()["needs_refresh"] is False
    assert response.json()["for_date"]
    assert response.json()["calendar"]["error_code"] == "google_timeout"
    assert response.json()["mail"]["error_code"] == "google_timeout"
    assert timeouts == [15.0, 0.05]


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
