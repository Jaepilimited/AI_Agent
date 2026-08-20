"""Security regression tests for JWT signing-key configuration."""

import pytest

from app.config import Settings
import app.main as main


@pytest.mark.parametrize(
    "secret",
    ["", "short-secret", "skin1004-ai-secret-change-me"],
)
def test_create_app_rejects_insecure_jwt_secrets(monkeypatch, secret: str) -> None:
    settings = Settings(_env_file=None, jwt_secret_key=secret)
    monkeypatch.setattr(main, "get_settings", lambda: settings)

    with pytest.raises(RuntimeError, match="JWT_SECRET_KEY"):
        main.create_app()


def test_create_app_accepts_explicit_strong_jwt_secret(monkeypatch) -> None:
    settings = Settings(_env_file=None, jwt_secret_key="t" * 64)
    monkeypatch.setattr(main, "get_settings", lambda: settings)

    assert main.create_app() is not None
