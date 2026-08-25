from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import auth_routes
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


def test_refresh_timeout_returns_safe_cached_sections(monkeypatch):
    async def timeout(*_args, **_kwargs):
        _args[0].close()
        raise TimeoutError()

    cached = {
        "enabled": True,
        "needs_refresh": True,
        "calendar": {"status": "ready", "items": [], "error_code": ""},
        "mail": {"status": "empty", "items": [], "error_code": ""},
    }
    monkeypatch.setattr("app.api.personal_briefing_api.asyncio.wait_for", timeout)
    monkeypatch.setattr("app.api.personal_briefing_api.get_cached_for_user", lambda _user: cached)

    response = TestClient(_app_for_owner()).post("/api/personal-briefing/refresh")

    assert response.status_code == 200
    assert response.json()["needs_refresh"] is False
    assert response.json()["calendar"] == {
        "status": "error", "items": [], "error_code": "google_timeout",
    }
    assert response.json()["mail"] == {
        "status": "error", "items": [], "error_code": "google_timeout",
    }


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
