"""Shared test-process configuration."""

import os

import pytest


# Application startup deliberately refuses missing or weak JWT keys. Tests use
# an explicit non-production key so importing app.main exercises the same guard.
os.environ.setdefault("JWT_SECRET_KEY", "test-only-jwt-secret-" + ("x" * 42))


@pytest.fixture
def password_login_on(monkeypatch):
    """로컬 ID/PW 경로를 **켠 채로** 도는 테스트용 (2026-09-08).

    회사 계정(Entra ID)으로 옮기면서 `password_login_enabled` 기본값이 False 가
    됐다. 비밀번호 경로 **자체**를 검증하는 회귀(로그인 지연, 관리자 임시
    비밀번호, 재설정 요청 접수…)는 스위치를 켜 두고 돌아야 한다 — 끄고 돌리면
    관문에서 403 으로 끝나 정작 재려던 것을 아무것도 재지 못한다.

    ⚠️ `app.config.get_settings` 는 `lru_cache` 싱글턴이다. 그 객체의 필드를
       직접 고치면 **다음 테스트로 샌다** — 실제 설정을 감싼 대역을 끼운다.
    ⛔ 이 픽스처를 "차단이 도는지" 보는 테스트에 쓰지 마라. 그쪽은
       `tests/test_password_login_disabled.py` 가 반대 방향으로 지킨다.
    """
    from app.api import auth_api
    from app.config import get_settings

    class _PasswordLoginOn:
        password_login_enabled = True

        def __init__(self, real):
            self._real = real

        def __getattr__(self, name):  # 나머지는 진짜 설정 그대로
            return getattr(self._real, name)

    real = get_settings()
    monkeypatch.setattr(auth_api, "get_settings", lambda: _PasswordLoginOn(real))
