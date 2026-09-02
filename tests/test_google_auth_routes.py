import base64
from datetime import datetime, timedelta, timezone

import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import auth_routes
from app.api.auth_middleware import get_current_user, get_optional_user
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

    # ⚠️ 콜백은 `get_optional_user` 를 쓴다 — 쿠키가 없을 때 401 대신 되돌려
    #    보내야 하기 때문이다. 둘 다 덮어야 로그인 상태를 흉내낼 수 있다
    app.dependency_overrides[get_current_user] = owner
    app.dependency_overrides[get_optional_user] = owner
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

    # ⚠️ Host 를 실제 접속 주소로 준다. 콜백 주소는 Host 헤더에서 만들어지고,
    #    구글에 등록할 수 없는 호스트면 보내기 전에 막히기 때문이다
    #    (2026-09-02: 사내 DNS `ai.cravercorp.internal` 로 접속한 사용자가 구글에서
    #    차단됐다). TestClient 기본값 `testserver` 는 그 조건에 걸린다.
    response = client.get("/auth/google/login", follow_redirects=False,
                          headers={"Host": "10.1.100.5"})

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
    client.app.dependency_overrides[get_optional_user] = unsafe_owner
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


# ── 콜백이 쿠키를 못 받아 401 이 되던 것 (2026-08-25 이해인 제보) ──────────────
#
# ⛔ 구글은 **원시 IP 를 리다이렉트 URI 로 받지 않는다.** 그래서 `_get_redirect_uri`
#    가 `10.1.100.5` → `10.1.100.5.nip.io` 로 바꿔서 등록한다. 그런데 세션 쿠키는
#    **호스트 전용**이라 사용자가 보고 있는 `10.1.100.5` 에만 붙는다. 구글이
#    되돌려 보내는 곳은 `10.1.100.5.nip.io` — **다른 호스트라 쿠키가 안 실린다.**
#
#    콜백에 로그인 요구(`get_current_user`)가 붙기 전에는 문제가 없었다. 붙은
#    당일(2026-08-25 10:00 커밋) 오후에 첫 사용자가 걸렸고, 그날 콜백 4건이 전부
#    401 이었다. **이미 연결된 사람은 토큰이 갱신만 되므로 아무도 눈치채지 못한다** —
#    새로 연결하는 사람만 막힌다.
#
# 고치는 방향: 쿠키가 없으면 **쿠키가 있는 호스트로 한 번 되돌려 보낸다.**
#   구글 → 10.1.100.5.nip.io/callback (쿠키 없음) → 302 → 10.1.100.5/callback (쿠키 실림)
#   ⚠️ 최상위 GET 이동이라 SameSite=Lax 쿠키가 실린다. 되돌린 뒤에도 세션이 없으면
#      그때는 사람이 읽을 수 있는 안내를 보여준다 (원시 401 JSON 금지).
#   ⛔ 되돌리는 것 말고는 아무것도 하지 않는다 — 인증 없이 코드를 교환하면
#      f1a3bbd 가 막으려던 것(남의 세션에 계정 붙이기)이 되돌아온다.

NIP_HOST = "10.1.100.5.nip.io"


def test_browsing_host_is_the_inverse_of_the_redirect_host():
    """`_get_redirect_uri` 가 바꾼 것을 그대로 되돌려야 한다 — 둘은 한 쌍이다."""
    assert auth_routes._browsing_host("10.1.100.5.nip.io") == "10.1.100.5"
    assert auth_routes._browsing_host("172.16.1.250.nip.io:3000") == "172.16.1.250:3000"
    # 사용자가 이미 그 호스트를 보고 있었으면 되돌릴 곳이 없다
    assert auth_routes._browsing_host("10.1.100.5") == ""
    assert auth_routes._browsing_host("chat.example.com") == ""


def test_callback_without_cookie_hops_to_the_host_that_has_it(anonymous_client, fake_manager):
    """쿠키가 없는 nip.io 콜백은 사용자가 보던 호스트로 되돌려 보낸다."""
    response = anonymous_client.get(
        "/auth/google/callback?code=abc&state=signed&scope=gmail.readonly",
        headers={"host": NIP_HOST},
        follow_redirects=False,
    )

    assert response.status_code in (302, 307)
    location = response.headers["location"]
    assert location.startswith("http://10.1.100.5/auth/google/callback?")
    # ⚠️ 코드·상태를 그대로 넘겨야 두 번째 요청이 교환을 끝낼 수 있다
    assert "code=abc" in location and "state=signed" in location
    assert "scope=gmail.readonly" in location
    assert "session_hop=1" in location
    assert fake_manager.seen_email == "", "되돌리기 단계에서 자격증명을 건드리면 안 된다"


def test_hop_happens_only_once(anonymous_client, fake_manager):
    """⛔ 되돌린 뒤에도 쿠키가 없으면 무한 왕복이 된다 — 한 번만 시도한다."""
    response = anonymous_client.get(
        "/auth/google/callback?code=abc&state=signed&session_hop=1",
        headers={"host": NIP_HOST},
        follow_redirects=False,
    )

    assert response.status_code == 401
    assert "location" not in response.headers
    assert fake_manager.seen_email == ""


def test_failed_hop_explains_itself_in_korean(anonymous_client):
    """원시 401 JSON 은 사용자가 무엇을 해야 할지 알 수 없다."""
    response = anonymous_client.get(
        "/auth/google/callback?code=abc&state=signed&session_hop=1",
        headers={"host": NIP_HOST},
        follow_redirects=False,
    )

    assert "text/html" in response.headers["content-type"]
    assert "로그인" in response.text


def test_authenticated_callback_never_hops(client, fake_manager, monkeypatch):
    """세션이 있으면 nip.io 에서도 그 자리에서 끝낸다 (왕복은 쿠키가 없을 때만)."""
    monkeypatch.setattr(
        auth_routes, "consume_state", lambda _state, _user_id: {"email": "owner@example.com"})

    response = client.get(
        "/auth/google/callback?code=abc&state=signed",
        headers={"host": NIP_HOST},
        follow_redirects=False,
    )

    assert response.status_code == 200
    assert fake_manager.seen_email == "owner@example.com"
