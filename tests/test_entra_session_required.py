"""Entra proof is mandatory, including old cookies and routes without dependencies."""

import time
from types import SimpleNamespace

import jwt
import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.api import auth_api, auth_middleware, middleware
from app.core import session_auth

SECRET = "test-only-entra-proof-" + "x" * 48
TENANT = "11111111-1111-4111-8111-111111111111"
OID = "22222222-2222-4222-8222-222222222222"


def token(**changes):
    claims = {
        "user_id": 7, "email": "employee@example.test", "role": "admin",
        "purpose": "session", "auth_provider": "entra", "entra_tid": TENANT,
        "entra_oid": OID, "exp": int(time.time()) + 3600,
    }
    claims.update(changes)
    return jwt.encode({k: v for k, v in claims.items() if v is not None}, SECRET, algorithm="HS256")


@pytest.fixture
def environment(monkeypatch):
    settings = SimpleNamespace(jwt_secret_key=SECRET, entra_tenant_id=TENANT,
                               password_login_enabled=False, cookie_secure=False)
    for module in (auth_api, auth_middleware, middleware, session_auth):
        monkeypatch.setattr(module, "get_settings", lambda: settings)
    auth_middleware.invalidate_user_cache()
    auth_api._me_last_refresh.clear()
    reads, visits, handlers = [], [], []

    def row(sql, params=()):
        reads.append(params)
        return {"id": 7, "email": "employee@example.test", "role": "admin",
                "ad_user_id": 70, "allowed_models": None, "entra_oid": OID,
                "account_active": 1, "directory_active": 1}

    async def visit(user_id):
        visits.append(user_id)

    monkeypatch.setattr(auth_middleware, "fetch_one", row)
    monkeypatch.setattr(auth_api, "_lookup_brand_filter", lambda uid: "SK,CBT")
    monkeypatch.setattr(auth_api, "_record_authenticated_visit", visit)
    monkeypatch.setattr(auth_api, "_survey_prompt_for", lambda uid: None)
    monkeypatch.setattr(auth_api, "fetch_all", lambda *a, **kw: [])

    app = FastAPI()
    app.add_middleware(middleware.RequestLoggingMiddleware)
    app.include_router(auth_api.auth_api_router)

    async def protected(request: Request):
        handlers.append(request.url.path)
        return {"user_id": getattr(request.state, "user_id", None)}

    for path in ("/", "/dashboard", "/coa-finder", "/face-search", "/face-search/stats",
                 "/face-search/query", "/face-search/thumb/private", "/harness", "/api/harness/tree",
                 "/api/harness/files", "/api/admin/directory/users", "/api/profile",
                 "/v1/chat/completions", "/frontend/chat.html", "/static/dashboard.html",
                 "/auth/google/status", "/auth/google/login"):
        app.add_api_route(path, protected, methods=["GET", "POST"])
    for path in ("/login", "/health", "/health/ready", "/auth/entra/status", "/auth/entra/login",
                 "/auth/entra/callback", "/users/auth/openid_connect/callback", "/auth/google/callback",
                 "/api/auth/google/callback", "/settings", "/frontend/auth.js", "/static/style.css"):
        app.add_api_route(path, protected, methods=["GET"])
    env = SimpleNamespace(app=app, settings=settings, reads=reads, visits=visits, handlers=handlers)
    yield env
    auth_middleware.invalidate_user_cache()
    auth_api._me_last_refresh.clear()


@pytest.mark.parametrize("changes", [
    {"auth_provider": None, "purpose": None},  # Old cookies, including old Entra logins.
    {"auth_provider": "password"},
    {"purpose": "google_oauth", "auth_provider": None},
    {"purpose": None}, {"entra_oid": None}, {"entra_oid": "invalid"},
    {"entra_tid": "33333333-3333-4333-8333-333333333333"},
    {"entra_tid": None}, {"exp": None}, {"exp": time.time() - 10},
    {"user_id": "7"}, {"user_id": True}, {"user_id": -1},
    {"auth_provider": "internal_monitor"},
])
def test_legacy_or_unproven_session_cannot_refresh_even_for_linked_cached_account(environment, changes):
    client = TestClient(environment.app)
    client.cookies.set("token", token())
    assert client.get("/api/profile").status_code == 200  # Cache already contains a linked user.
    assert len(environment.reads) == 1
    client.cookies.clear()
    client.cookies.set("token", token(**changes))
    response = client.get("/api/auth/me")
    assert response.status_code == 401
    assert response.headers["X-Cella-Auth"] == "login-required"
    assert "Max-Age=0" in response.headers.get("set-cookie", "")
    assert environment.visits == [] and auth_api._me_last_refresh == {}
    assert len(environment.reads) == 1  # Rejected before cache/DB lookup.


def test_valid_entra_refresh_preserves_proof_and_permissions(environment):
    client = TestClient(environment.app)
    client.cookies.set("token", token())
    response = client.get("/api/auth/me")
    assert response.status_code == 200
    claims = session_auth.decode_session(response.cookies["token"], environment.settings)
    assert (claims["auth_provider"], claims["purpose"], claims["entra_tid"], claims["entra_oid"]) == (
        "entra", "session", TENANT, OID)
    assert claims["user_id"] == 7 and claims["role"] == "admin" and claims["brand_filter"] == "SK,CBT"
    assert response.json()["can_view_fi"] is True
    assert environment.visits == [7]
    assert client.get("/api/admin/directory/users").status_code == 200


@pytest.mark.parametrize("path", ["/", "/dashboard", "/coa-finder", "/face-search", "/harness",
                                 "/frontend/chat.html", "/static/dashboard.html", "/auth/google/login"])
def test_application_pages_redirect_an_invalid_cookie_instead_of_serving_html(environment, path):
    client = TestClient(environment.app)
    client.cookies.set("token", "not-a-session")
    response = client.get(path, follow_redirects=False)
    assert response.status_code == 302 and response.headers["location"] == "/login"
    assert environment.handlers == []


@pytest.mark.parametrize("method,path", [
    ("GET", "/api/admin/directory/users"), ("GET", "/api/profile"),
    ("POST", "/v1/chat/completions"), ("POST", "/face-search/query"),
    ("GET", "/face-search/stats"), ("GET", "/face-search/thumb/private"),
    ("GET", "/api/harness/tree"), ("POST", "/api/harness/files"),
    ("GET", "/auth/google/status"),
    ("GET", "/api/auth/departments"), ("GET", "/api/auth/users-by-dept"),
    ("GET", "/api/auth/search-name"), ("GET", "/openapi.json"),
])
def test_unprotected_routers_and_roster_are_covered_by_the_session_gate(environment, method, path):
    response = TestClient(environment.app).request(method, path)
    assert response.status_code == 401
    assert response.headers["X-Cella-Auth"] == "login-required"
    assert environment.handlers == [] and environment.reads == []


@pytest.mark.parametrize("path", [
    "/login", "/health", "/health/ready", "/auth/entra/status", "/auth/entra/login",
    "/auth/entra/callback", "/users/auth/openid_connect/callback", "/auth/google/callback",
    "/api/auth/google/callback", "/settings", "/frontend/auth.js", "/static/style.css",
])
def test_login_callbacks_health_and_assets_do_not_require_an_existing_session(environment, path):
    response = TestClient(environment.app).get(path, follow_redirects=False)
    expected = 302 if path == "/api/auth/google/callback" else 200
    assert response.status_code == expected
    assert "X-Cella-Auth" not in response.headers
    assert environment.reads == []


def test_disabled_password_handlers_keep_their_existing_company_login_message(environment):
    client = TestClient(environment.app)
    response = client.post("/api/auth/signin", json={"department": "test", "name": "test", "password": "test"})
    assert response.status_code == 403 and "회사 계정" in response.json()["detail"]
    assert "X-Cella-Auth" not in response.headers and environment.reads == []


@pytest.mark.parametrize("method,path", sorted(middleware._RELAY_ROUTES))
def test_machine_routes_reach_their_own_authenticator(environment, method, path):
    from app.api.jandi_briefing_api import router
    environment.app.include_router(router)
    response = TestClient(environment.app).request(method, path, json={})
    # Missing relay token is rejected by the relay guard, never admitted anonymously.
    assert response.status_code == 404
    assert "X-Cella-Auth" not in response.headers and environment.reads == []


@pytest.mark.parametrize("host,method,path,headers,allowed", [
    ("127.0.0.1", "POST", "/v1/chat/completions", {}, True),
    ("::1", "POST", "/v1/chat/completions", {}, True),
    ("10.1.100.5", "POST", "/v1/chat/completions", {}, False),
    ("127.0.0.1", "GET", "/v1/chat/completions", {}, False),
    ("127.0.0.1", "POST", "/api/admin/directory/users", {}, False),
    ("127.0.0.1", "GET", "/api/auth/me", {}, False),
    ("127.0.0.1", "POST", "/v1/chat/completions", {"X-Forwarded-For": "127.0.0.1"}, False),
    ("127.0.0.1", "POST", "/v1/chat/completions", {"Forwarded": "for=127.0.0.1"}, False),
    ("127.0.0.1", "POST", "/v1/chat/completions", {"X-Forwarded-Proto": "https"}, False),
])
def test_monitor_tokens_cannot_become_browser_or_remote_sessions(environment, host, method, path, headers, allowed):
    monitor = session_auth.create_service_token(7, "monitor@example.test", "admin", service="self_check")
    client = TestClient(environment.app, client=(host, 12345))
    client.cookies.set("token", monitor)
    response = client.request(method, path, headers=headers)
    assert response.status_code == (200 if allowed else 401)
    assert environment.visits == [] and auth_api._me_last_refresh == {}
    if not allowed:
        assert environment.handlers == []


def test_explicit_password_rollback_mode_still_accepts_legacy_sessions(environment):
    environment.settings.password_login_enabled = True
    client = TestClient(environment.app)
    client.cookies.set("token", token(auth_provider=None, purpose=None))
    response = client.get("/api/auth/me")
    assert response.status_code == 200
    claims = jwt.decode(response.cookies["token"], SECRET, algorithms=["HS256"])
    assert claims["auth_provider"] == "password" and "entra_oid" not in claims
    environment.settings.password_login_enabled = False
    assert client.get("/api/profile").status_code == 401


def test_actual_application_registers_the_gate_before_private_handlers(environment, monkeypatch):
    environment.settings.migrated_redirect_url = ""
    environment.settings.cors_origins = "http://testserver"
    from app import main
    monkeypatch.setattr(main, "get_settings", lambda: environment.settings)
    # Do not enter lifespan: no schedulers, provisioning, or external services.
    client = TestClient(main.create_app())
    for method, path in [("POST", "/face-search/query"), ("GET", "/face-search/stats"),
                         ("GET", "/api/harness/tree"), ("POST", "/v1/chat/completions"),
                         ("GET", "/api/admin/directory/users")]:
        response = client.request(method, path)
        assert response.status_code == 401 and response.headers["X-Cella-Auth"] == "login-required"
    for path in ("/", "/coa-finder", "/face-search", "/harness", "/frontend/chat.html"):
        response = client.get(path, follow_redirects=False)
        assert response.status_code == 302 and response.headers["location"] == "/login"
    assert client.get("/login").status_code == 200
    client.cookies.set("token", token())
    assert client.get("/").status_code == 200
    assert client.get("/coa-finder").status_code == 200
