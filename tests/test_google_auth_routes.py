import base64
from datetime import datetime, timedelta, timezone

import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import auth_routes
from app.api.auth_middleware import get_current_user
from app.core import google_oauth_state
from app.core import google_auth
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
    assert any(
        sql.startswith("DELETE FROM google_oauth_states WHERE nonce_hash")
        and params[1] == 7
        for sql, params in writes
    )


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


def test_oauth_state_replay_is_rejected(monkeypatch):
    """The database compare-and-set makes the same state unusable after one callback."""
    updates = iter((1, 0))

    def execute(sql, _params=()):
        return next(updates) if sql.startswith("DELETE FROM google_oauth_states WHERE nonce_hash") else 1

    monkeypatch.setattr(google_oauth_state, "execute", execute)
    monkeypatch.setattr(
        google_oauth_state,
        "get_settings",
        lambda: type("Settings", (), {"jwt_secret_key": "s" * 64})(),
    )
    now = datetime(2026, 8, 25, 0, 0, tzinfo=UTC)
    token = google_oauth_state.issue_state(7, "owner@example.com", now=now)

    assert google_oauth_state.consume_state(token, 7, now=now + timedelta(minutes=1))["user_id"] == 7
    with pytest.raises(ValueError, match="already used"):
        google_oauth_state.consume_state(token, 7, now=now + timedelta(minutes=1))


def test_oauth_state_expiry_is_rejected_before_consumption(monkeypatch):
    """An expired signed state cannot be exchanged even when its nonce exists."""
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
    now = datetime(2026, 8, 25, 0, 0, tzinfo=UTC)
    token = google_oauth_state.issue_state(7, "owner@example.com", now=now)

    with pytest.raises(ValueError, match="expired"):
        google_oauth_state.consume_state(token, 7, now=now + timedelta(minutes=11))
    assert not any("WHERE nonce_hash" in sql for sql, _ in writes)


def test_oauth_state_rejects_tampered_signature(monkeypatch):
    """A state altered after issuance fails signature validation before its nonce is used."""
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
    token = google_oauth_state.issue_state(7, "owner@example.com")
    header, payload, encoded_signature = token.split(".")
    padding = "=" * (-len(encoded_signature) % 4)
    signature = bytearray(base64.urlsafe_b64decode(encoded_signature + padding))
    signature[0] ^= 0x01
    tampered_signature = base64.urlsafe_b64encode(bytes(signature)).decode("ascii").rstrip("=")
    tampered = f"{header}.{payload}.{tampered_signature}"

    with pytest.raises(jwt.InvalidTokenError):
        google_oauth_state.consume_state(tampered, 7)
    assert not any("WHERE nonce_hash" in sql for sql, _ in writes)


def test_oauth_state_consumption_uses_database_utc(monkeypatch):
    """Nonce consumption succeeds only through the UTC database-time comparison."""
    def execute(sql, _params=()):
        if sql.startswith("DELETE FROM google_oauth_states WHERE nonce_hash"):
            return 1 if "UTC_TIMESTAMP()" in sql else 0
        return 1

    monkeypatch.setattr(google_oauth_state, "execute", execute)
    monkeypatch.setattr(
        google_oauth_state,
        "get_settings",
        lambda: type("Settings", (), {"jwt_secret_key": "s" * 64})(),
    )
    now = datetime(2026, 8, 25, 0, 0, tzinfo=UTC)
    token = google_oauth_state.issue_state(7, "owner@example.com", now=now)

    assert google_oauth_state.consume_state(token, 7, now=now + timedelta(minutes=1))["email"] == "owner@example.com"


def test_oauth_state_deletes_consumed_identity_and_cleans_old_rows(monkeypatch):
    """Consumed/expired nonce rows must not retain an internal email in MariaDB."""
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
    now = datetime(2026, 8, 25, 0, 0, tzinfo=UTC)

    token = google_oauth_state.issue_state(7, "owner@example.com", now=now)
    google_oauth_state.consume_state(token, 7, now=now + timedelta(minutes=1))

    cleanup = [sql for sql, _ in writes if "expires_at < UTC_TIMESTAMP()" in sql]
    consume = [sql for sql, _ in writes if sql.startswith("DELETE FROM google_oauth_states WHERE nonce_hash")]
    assert len(cleanup) >= 2
    assert len(consume) == 1
    assert "used_at IS NOT NULL" in cleanup[0]


def test_google_auth_logs_only_safe_error_metadata(monkeypatch, tmp_path):
    """Credential refresh failures must not log identities, raw errors, or content."""
    token_path = tmp_path / "token.json"
    token_path.write_text("{}", encoding="utf-8")
    manager = object.__new__(google_auth.GoogleAuthManager)
    manager._token_path = lambda _email: token_path

    class BadCredentials:
        refresh_token = "refresh-token"

        def refresh(self, _request):
            raise RuntimeError("raw-secret owner@example.com mail subject")

    calls = []

    class CaptureLogger:
        def info(self, event, **values):
            calls.append((event, values))

        warning = info
        error = info

    monkeypatch.setattr(
        google_auth.Credentials,
        "from_authorized_user_file",
        lambda *_args, **_kwargs: BadCredentials(),
    )
    monkeypatch.setattr(google_auth, "logger", CaptureLogger())

    assert manager._get_credentials_from_file("owner@example.com") is None
    rendered = repr(calls)
    assert "owner@example.com" not in rendered
    assert "raw-secret" not in rendered
    assert "mail subject" not in rendered
    assert calls == [("token_load_failed", {"source": "file", "error_type": "RuntimeError"})]


def test_credential_load_outcome_distinguishes_invalid_grant(monkeypatch, tmp_path):
    """A definitive OAuth revocation is distinguishable without deleting the token here."""
    token_path = tmp_path / "token.json"
    token_path.write_text("{}", encoding="utf-8")
    manager = object.__new__(google_auth.GoogleAuthManager)
    manager._token_path = lambda _email: token_path

    class RevokedCredentials:
        refresh_token = "refresh-token"

        def refresh(self, _request):
            raise RuntimeError("invalid_grant")

    monkeypatch.setattr(
        google_auth.Credentials,
        "from_authorized_user_file",
        lambda *_args, **_kwargs: RevokedCredentials(),
    )

    outcome = manager.load_credentials("owner@example.com")

    assert outcome.status == "invalid"
    assert outcome.credentials is None
    assert outcome.definitive_disconnect is True
    assert token_path.exists()


def test_credential_load_outcome_preserves_transient_failure(monkeypatch, tmp_path):
    """Transport/temporary refresh errors remain retryable and keep stored credentials."""
    token_path = tmp_path / "token.json"
    token_path.write_text("{}", encoding="utf-8")
    manager = object.__new__(google_auth.GoogleAuthManager)
    manager._token_path = lambda _email: token_path

    class TemporarilyUnavailableCredentials:
        refresh_token = "refresh-token"

        def refresh(self, _request):
            raise TimeoutError("temporary transport failure")

    monkeypatch.setattr(
        google_auth.Credentials,
        "from_authorized_user_file",
        lambda *_args, **_kwargs: TemporarilyUnavailableCredentials(),
    )

    outcome = manager.load_credentials("owner@example.com")

    assert outcome.status == "transient_error"
    assert outcome.credentials is None
    assert outcome.definitive_disconnect is False
    assert token_path.exists()


def test_credential_load_outcome_treats_temporary_parse_failure_as_transient(monkeypatch, tmp_path):
    """An unreadable token is preserved because a partial/temporary read may recover."""
    token_path = tmp_path / "token.json"
    token_path.write_text("partial", encoding="utf-8")
    manager = object.__new__(google_auth.GoogleAuthManager)
    manager._token_path = lambda _email: token_path
    monkeypatch.setattr(
        google_auth.Credentials,
        "from_authorized_user_file",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("partial json")),
    )

    outcome = manager.load_credentials("owner@example.com")

    assert outcome.status == "transient_error"
    assert outcome.definitive_disconnect is False
    assert token_path.read_text(encoding="utf-8") == "partial"


def test_credentials_only_wrapper_preserves_openwebui_fallback():
    """Existing GWS callers still try Open WebUI when local credentials are unavailable."""
    manager = object.__new__(google_auth.GoogleAuthManager)
    fallback_credentials = object()
    manager._get_credentials_from_file = lambda _email: None
    manager._get_credentials_from_openwebui = lambda _email: fallback_credentials

    assert manager.get_credentials("owner@example.com") is fallback_credentials


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

    def get_auth_url(self, user_email, *, state, redirect_uri=""):
        self.seen_email = user_email
        return "https://accounts.google.com/o/oauth2/auth?state=" + state


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


def test_authenticated_login_route_matches_frontend_contract(client, fake_manager, monkeypatch):
    monkeypatch.setattr(auth_routes, "issue_state", lambda _uid, _email: "signed")

    response = client.get("/auth/google/login", follow_redirects=False)

    assert response.status_code == 307
    assert response.headers["location"].endswith("state=signed")
    assert fake_manager.seen_email == "owner@example.com"


def test_oauth_routes_require_auth(anonymous_client):
    """Credential-changing or credential-disclosing routes require a JWT user."""
    assert anonymous_client.get("/auth/google/status").status_code == 401
    assert anonymous_client.get("/auth/google/login").status_code == 401
    assert anonymous_client.post("/auth/google/revoke").status_code == 401
    assert anonymous_client.get("/auth/google/callback?code=code&state=state").status_code == 401


def test_callback_uses_signed_state_email_and_escapes_display(client, fake_manager, monkeypatch):
    """The callback saves only the state-bound account and displays escaped JWT identity."""
    async def unsafe_owner():
        return User(id=7, email='<img src=x onerror="alert(1)">')

    client.app.dependency_overrides[get_current_user] = unsafe_owner
    monkeypatch.setattr(
        auth_routes,
        "consume_state",
        lambda _state, _user_id: {"email": '<img src=x onerror="alert(1)">'},
    )

    response = client.get("/auth/google/callback?code=code&state=signed")

    assert response.status_code == 200
    assert fake_manager.seen_email == "<img src=x onerror=\"alert(1)\">"
    assert '<img src=x onerror="alert(1)">' not in response.text
    assert "&lt;img src=x onerror=&quot;alert(1)&quot;&gt;" in response.text


def test_callback_rejects_state_for_prior_application_identity(client, fake_manager, monkeypatch):
    """The signed state email must still match the active JWT owner at callback time."""
    monkeypatch.setattr(
        auth_routes,
        "consume_state",
        lambda _state, _user_id: {"email": "prior-owner@example.com"},
    )

    response = client.get("/auth/google/callback?code=code&state=signed")

    assert response.status_code == 400
    assert fake_manager.seen_email == ""
