from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import auth_routes
from app.api.auth_middleware import get_current_user
from app.core import google_oauth_state
from app.db.models import User


UTC = timezone.utc


def test_oauth_state_is_tied_to_current_user_and_single_use(monkeypatch):
    """A state nonce can be consumed only by its issuing authenticated user."""
    now = datetime(2026, 8, 25, 0, 0, tzinfo=UTC)
    writes = []
    monkeypatch.setattr(
        google_oauth_state,
        "execute",
        lambda sql, params=(): writes.append((sql, params)) or 1,
    )
    monkeypatch.setattr(
        google_oauth_state,
        "get_settings",
        lambda: type("Settings", (), {"jwt_secret_key": "s" * 64})(),
    )

    token = google_oauth_state.issue_state(7, "owner@example.com", now=now)
    payload = google_oauth_state.consume_state(token, 7, now=now + timedelta(minutes=1))

    assert payload["user_id"] == 7
    assert payload["email"] == "owner@example.com"
    assert any("used_at IS NULL" in sql and params[1] == 7 for sql, params in writes)


def test_oauth_state_rejects_other_user(monkeypatch):
    """A signed state cannot be replayed from a different JWT session."""
    monkeypatch.setattr(google_oauth_state, "execute", lambda *_args, **_kwargs: 1)
    monkeypatch.setattr(
        google_oauth_state,
        "get_settings",
        lambda: type("Settings", (), {"jwt_secret_key": "s" * 64})(),
    )

    token = google_oauth_state.issue_state(7, "owner@example.com")

    with pytest.raises(ValueError, match="user"):
        google_oauth_state.consume_state(token, 8)


class _FakeAuthManager:
    def __init__(self):
        self.seen_email = ""

    def has_credentials(self, user_email):
        self.seen_email = user_email
        return True

    def get_stored_google_email(self, user_email):
        self.seen_email = user_email
        return "connected@example.com"

    def revoke_credentials(self, user_email):
        self.seen_email = user_email
        return True

    def exchange_code(self, _code, user_email, redirect_uri=""):
        self.seen_email = user_email


@pytest.fixture
def fake_manager(monkeypatch):
    manager = _FakeAuthManager()
    monkeypatch.setattr(auth_routes, "_get_auth_manager", lambda: manager)
    return manager


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(auth_routes.auth_router)

    async def owner():
        return User(id=7, email="owner@example.com")

    app.dependency_overrides[get_current_user] = owner
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def anonymous_client():
    app = FastAPI()
    app.include_router(auth_routes.auth_router)
    return TestClient(app, raise_server_exceptions=False)


def test_status_ignores_injected_email(client, fake_manager):
    """A query parameter cannot select credentials belonging to another user."""
    response = client.get("/auth/google/status?user_email=other@example.com")

    assert response.status_code == 200
    assert fake_manager.seen_email == "owner@example.com"


def test_oauth_routes_require_auth(anonymous_client):
    """Credential-changing or credential-disclosing routes require a JWT user."""
    assert anonymous_client.get("/auth/google/status").status_code == 401
    assert anonymous_client.get("/auth/google/login").status_code == 401
    assert anonymous_client.post("/auth/google/revoke").status_code == 401


def test_callback_uses_signed_state_email_and_escapes_display(client, fake_manager, monkeypatch):
    """The callback saves only the state-bound account and displays escaped JWT identity."""
    async def unsafe_owner():
        return User(id=7, email='<img src=x onerror="alert(1)">')

    client.app.dependency_overrides[get_current_user] = unsafe_owner
    monkeypatch.setattr(
        auth_routes,
        "consume_state",
        lambda _state, _user_id: {"email": "credential@example.com"},
    )

    response = client.get("/auth/google/callback?code=code&state=signed")

    assert response.status_code == 200
    assert fake_manager.seen_email == "credential@example.com"
    assert '<img src=x onerror="alert(1)">' not in response.text
    assert "&lt;img src=x onerror=&quot;alert(1)&quot;&gt;" in response.text
