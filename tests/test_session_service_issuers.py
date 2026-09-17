"""Scheduled monitors issue scoped service proof without contacting the app."""

from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import jwt
import pytest

from app import config
from app.core import golden_runner, self_check, session_auth


SECRET = "test-only-service-issuer-secret-" + ("x" * 40)


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch):
    settings = SimpleNamespace(
        jwt_secret_key=SECRET, port=12345, password_login_enabled=False,
    )
    monkeypatch.setattr(config, "get_settings", lambda: settings)
    monkeypatch.setattr(session_auth, "get_settings", lambda: settings)


def _service_claims(token, *, service, lifetime):
    claims = jwt.decode(
        token, SECRET, algorithms=["HS256"],
        options={"require": ["user_id", "email", "role", "iat", "exp"]},
    )
    assert claims["purpose"] == "service"
    assert claims["auth_provider"] == "internal_monitor"
    assert claims["scope"] == "chat:probe"
    assert claims["service"] == service
    assert claims["exp"] - claims["iat"] == lifetime
    return claims


def test_golden_user_reuses_the_existing_non_admin_account(monkeypatch):
    lookup = Mock(return_value={"id": 41})
    create = Mock(side_effect=AssertionError("Existing monitor account was recreated"))
    monkeypatch.setattr(golden_runner, "fetch_one", lookup)
    monkeypatch.setattr(golden_runner, "execute_lastid", create)

    claims = _service_claims(
        golden_runner._make_token("user"), service="golden_runner", lifetime=7200,
    )

    assert claims["user_id"] == 41
    assert claims["role"] == "user"
    assert claims["email"] == golden_runner.GOLDEN_EMAIL
    assert lookup.call_count == 1
    assert lookup.call_args.args[1] == (golden_runner.GOLDEN_EMAIL,)
    create.assert_not_called()


def test_golden_user_creates_and_uses_its_own_account_when_missing(monkeypatch):
    monkeypatch.setattr(golden_runner, "fetch_one", Mock(return_value=None))
    create = Mock(return_value=42)
    monkeypatch.setattr(golden_runner, "execute_lastid", create)

    claims = _service_claims(
        golden_runner._make_token("user"), service="golden_runner", lifetime=7200,
    )

    assert claims["user_id"] == 42
    assert claims["role"] == "user"
    assert claims["email"] == golden_runner.GOLDEN_EMAIL
    assert create.call_count == 1
    assert create.call_args.args[1][0] == golden_runner.GOLDEN_EMAIL
    assert create.call_args.args[1][1] == "!golden-no-login"


@pytest.mark.parametrize("admin_row, expected_id", [({"id": 73}, 73), (None, 1)])
def test_golden_admin_preserves_account_selection_and_monitor_identity(
    monkeypatch, admin_row, expected_id,
):
    lookup = Mock(return_value=admin_row)
    monkeypatch.setattr(golden_runner, "fetch_one", lookup)
    create = Mock(side_effect=AssertionError("Admin probe created a monitor user"))
    monkeypatch.setattr(golden_runner, "execute_lastid", create)

    claims = _service_claims(
        golden_runner._make_token("admin"), service="golden_runner", lifetime=7200,
    )

    assert claims["user_id"] == expected_id
    assert claims["role"] == "admin"
    assert claims["email"] == golden_runner.GOLDEN_EMAIL
    assert lookup.call_count == 1
    create.assert_not_called()


def test_canary_sends_short_lived_service_proof_with_the_selected_admin(monkeypatch):
    monkeypatch.setattr(
        self_check, "fetch_one",
        Mock(return_value={"id": 91, "email": "admin@example.test", "role": "admin"}),
    )
    requests = []
    client_options = []

    class CaptureClient:
        def __init__(self, **kwargs):
            client_options.append(kwargs)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, path, **kwargs):
            requests.append((path, kwargs))
            return httpx.Response(200, json={
                "choices": [{"message": {"content": "A complete answer. " * 20}}],
            })

    monkeypatch.setattr(httpx, "Client", CaptureClient)

    result = self_check._check_canary_answers()

    assert result.ok, result.detail
    assert client_options == [{"base_url": "http://127.0.0.1:12345"}]
    assert len(requests) == len(self_check._CANARY_QUESTIONS)
    tokens = set()
    for path, kwargs in requests:
        assert path == "/v1/chat/completions"
        assert not kwargs.get("headers")
        token = kwargs["cookies"]["token"]
        tokens.add(token)
        claims = _service_claims(token, service="self_check", lifetime=900)
        assert claims["user_id"] == 91
        assert claims["role"] == "admin"
        assert claims["email"] == "admin@example.test"
    assert len(tokens) == 1


def test_canary_without_an_admin_never_issues_a_token_or_opens_a_client(monkeypatch):
    monkeypatch.setattr(self_check, "fetch_one", Mock(return_value=None))
    issuer = Mock(side_effect=AssertionError("A canary without an account issued proof"))
    client = Mock(side_effect=AssertionError("A canary without an account opened HTTP"))
    monkeypatch.setattr(session_auth, "create_service_token", issuer)
    monkeypatch.setattr(httpx, "Client", client)

    result = self_check._check_canary_answers()

    assert not result.ok
    assert "admin" in result.detail
    issuer.assert_not_called()
    client.assert_not_called()
