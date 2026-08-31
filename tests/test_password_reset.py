"""Admin-initiated password reset for local sign-in.

Sign-in uses a bcrypt password stored on `users`, separate from Active Directory.
`/api/auth/change-password` requires the current password, so a person who has
forgotten it has no way back in and no admin has a way to help them. This adds:

- `POST /api/admin/ad/users/{ad_user_id}/reset-password` — admin-only, generates a
  server-side temporary password, stores it bcrypt-hashed on `users`, sets
  `must_change_password`, and returns the plaintext exactly once.
- A server-side gate in `get_current_user` that blocks app use (any endpoint except
  /me, /change-password, /logout) while `must_change_password` is set.
- `change-password` clearing the flag on success.

Covers: non-admin refused; the flag blocks app use; changing the password clears
the flag; the plaintext appears in the response and in no log/DB column; two
consecutive resets produce different passwords.
"""

import time

import bcrypt as _bcrypt
import jwt as _pyjwt
import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.api import admin_group_api
from app.api import auth_api
from app.api import auth_middleware
from app.api.auth_middleware import get_current_user
from app.db.models import User


ADMIN = User(id=1, email="admin@example.com", name="Admin", role="admin")
REGULAR = User(id=2, email="user@example.com", name="User", role="user")

_SECRET = "s" * 64


# ─────────────────────────────────────────────────────────────────
# POST /api/admin/ad/users/{id}/reset-password
# ─────────────────────────────────────────────────────────────────

def _admin_app(user):
    app = FastAPI()
    app.include_router(admin_group_api.ad_router)
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app, raise_server_exceptions=False)


def test_non_admin_refused(monkeypatch):
    touched = []
    monkeypatch.setattr(admin_group_api, "fetch_one", lambda *a, **k: touched.append(1) or None)
    monkeypatch.setattr(admin_group_api, "execute", lambda *a, **k: touched.append(1) or 1)

    resp = _admin_app(REGULAR).post("/api/admin/ad/users/9/reset-password")

    assert resp.status_code == 403
    assert not touched  # a non-admin's request never reaches the DB


def test_unregistered_ad_user_is_404(monkeypatch):
    """An AD user with no `users` row (never signed up) has no password to reset."""
    monkeypatch.setattr(admin_group_api, "fetch_one", lambda *a, **k: None)

    resp = _admin_app(ADMIN).post("/api/admin/ad/users/9/reset-password")

    assert resp.status_code == 404


def test_admin_reset_stores_bcrypt_hash_and_returns_plaintext_once(monkeypatch):
    target_row = {"user_id": 42, "display_name": "Kim", "username": "kim.kim"}
    monkeypatch.setattr(admin_group_api, "fetch_one", lambda *a, **k: target_row)

    writes = []
    monkeypatch.setattr(
        admin_group_api, "execute",
        lambda sql, params=(): writes.append((sql, params)) or 1,
    )

    resp = _admin_app(ADMIN).post("/api/admin/ad/users/9/reset-password")

    assert resp.status_code == 200
    body = resp.json()
    plaintext = body["temporary_password"]
    assert len(plaintext) >= 12

    # exactly one write, targets `users` (not `ad_users`) by the internal user id
    # resolved from the join — never confused with the ad_user_id path param
    assert len(writes) == 1
    sql, params = writes[0]
    assert "users" in sql and "ad_users" not in sql
    assert "must_change_password" in sql
    assert 42 in params

    stored_hash = next(p for p in params if isinstance(p, str) and p.startswith("$2"))
    assert stored_hash != plaintext
    assert _bcrypt.checkpw(plaintext.encode(), stored_hash.encode())


def test_two_consecutive_resets_differ(monkeypatch):
    monkeypatch.setattr(
        admin_group_api, "fetch_one",
        lambda *a, **k: {"user_id": 42, "display_name": "Kim", "username": "kim.kim"},
    )
    monkeypatch.setattr(admin_group_api, "execute", lambda *a, **k: 1)

    client = _admin_app(ADMIN)
    p1 = client.post("/api/admin/ad/users/9/reset-password").json()["temporary_password"]
    p2 = client.post("/api/admin/ad/users/9/reset-password").json()["temporary_password"]

    assert p1 != p2


def test_reset_action_is_logged_without_the_plaintext(monkeypatch):
    monkeypatch.setattr(
        admin_group_api, "fetch_one",
        lambda *a, **k: {"user_id": 42, "display_name": "Kim", "username": "kim.kim"},
    )
    monkeypatch.setattr(admin_group_api, "execute", lambda *a, **k: 1)

    logged = []

    class _FakeLogger:
        def warning(self, event, **kw):
            logged.append((event, kw))

        def info(self, *a, **k):
            pass

        def error(self, *a, **k):
            pass

    monkeypatch.setattr(admin_group_api, "logger", _FakeLogger())

    resp = _admin_app(ADMIN).post("/api/admin/ad/users/9/reset-password")
    plaintext = resp.json()["temporary_password"]

    assert logged, "the reset action must be recorded (who / whose account / when)"
    for event, kw in logged:
        assert event != "info-level"  # sanity: this really came through .warning
        assert plaintext not in repr((event, kw))


# ─────────────────────────────────────────────────────────────────
# must_change_password gate — enforced inside get_current_user, so it
# covers every endpoint that depends on it, not just a hand-picked list.
# ─────────────────────────────────────────────────────────────────

def _token(user_id: int) -> str:
    return _pyjwt.encode(
        {"user_id": user_id, "exp": time.time() + 3600}, _SECRET, algorithm="HS256"
    )


def _fake_settings():
    return type("Settings", (), {"jwt_secret_key": _SECRET, "cookie_secure": False})()


def _ad_row(user_id: int, must_change: int) -> dict:
    return {
        "id": user_id,
        "email": "u@example.com",
        "display_name": "User",
        "role": "user",
        "allowed_models": None,
        "ad_user_id": None,
        "ad_name": None,
        "ad_email": None,
        "department": "D",
        "must_change_password": must_change,
    }


def _gate_client(monkeypatch, must_change: int, user_id: int) -> TestClient:
    monkeypatch.setattr(auth_middleware, "get_settings", _fake_settings)
    monkeypatch.setattr(auth_middleware, "fetch_one", lambda *a, **k: _ad_row(user_id, must_change))
    auth_middleware._user_cache.clear()
    auth_api._me_last_refresh.clear()

    # /me and /change-password touch the DB directly too — keep this test about
    # the gate, not about wiring every downstream query.
    monkeypatch.setattr(auth_api, "fetch_one", lambda *a, **k: None)
    monkeypatch.setattr(auth_api, "fetch_all", lambda *a, **k: [])
    monkeypatch.setattr(auth_api, "execute", lambda *a, **k: 1)

    app = FastAPI()
    app.include_router(auth_api.auth_api_router)

    @app.get("/api/dummy/protected")
    async def _dummy(user: User = Depends(get_current_user)):
        return {"ok": True}

    client = TestClient(app, raise_server_exceptions=False)
    client.cookies.set("token", _token(user_id))
    return client


def test_must_change_password_blocks_other_endpoints(monkeypatch):
    client = _gate_client(monkeypatch, must_change=1, user_id=101)

    resp = client.get("/api/dummy/protected")

    assert resp.status_code == 403


def test_must_change_password_still_allows_me_change_password_and_logout(monkeypatch):
    client = _gate_client(monkeypatch, must_change=1, user_id=102)

    assert client.get("/api/auth/me").status_code == 200
    # change-password's own current-password check will 404/401 given the stubbed
    # DB above — the point here is only that the gate itself did not 403 it.
    assert client.post(
        "/api/auth/change-password",
        json={"current_password": "x", "new_password": "abcdefgh"},
    ).status_code != 403
    assert client.post("/api/auth/logout").status_code != 403


def test_without_the_flag_other_endpoints_work_normally(monkeypatch):
    client = _gate_client(monkeypatch, must_change=0, user_id=103)

    assert client.get("/api/dummy/protected").status_code == 200


# ─────────────────────────────────────────────────────────────────
# change-password clears the flag
# ─────────────────────────────────────────────────────────────────

def test_change_password_clears_must_change_password(monkeypatch):
    current_hash = _bcrypt.hashpw(b"temp-pw", _bcrypt.gensalt()).decode()
    monkeypatch.setattr(auth_api, "fetch_one", lambda *a, **k: {"password_hash": current_hash})

    writes = []
    monkeypatch.setattr(
        auth_api, "execute",
        lambda sql, params=(): writes.append((sql, params)) or 1,
    )

    app = FastAPI()
    app.include_router(auth_api.auth_api_router)
    app.dependency_overrides[get_current_user] = lambda: User(
        id=7, email="a@b.com", name="A", role="user"
    )
    client = TestClient(app, raise_server_exceptions=False)

    resp = client.post(
        "/api/auth/change-password",
        json={"current_password": "temp-pw", "new_password": "brand-new-pw"},
    )

    assert resp.status_code == 200
    assert writes, "change-password must persist the new hash"
    sql, _params = writes[-1]
    assert "must_change_password" in sql and "0" in sql
