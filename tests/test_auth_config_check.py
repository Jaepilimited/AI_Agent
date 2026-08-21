"""인증 설정 자가 점검 — 2026-08-21.

⛔ `JWT_SECRET_KEY` 를 필수로 만드는 변경이 들어왔는데, `.env` 는 **배포 대상이 아니다.**
   서버 값을 먼저 채우지 않고 코드만 올리면 그 순간부터 전 직원의 로그인이 죽는다.
   그런데 `/health` 는 인증을 안 타므로 **배포는 성공한 것처럼 보인다** — 사용자가
   로그인을 시도해야만 드러난다. 이 검사가 그 구간을 없앤다.
"""

from __future__ import annotations

import pytest

from app.core import self_check


class _S:
    def __init__(self, key):
        self.jwt_secret_key = key


def test_valid_secret_passes(monkeypatch):
    from app import config
    monkeypatch.setattr(config, "get_settings", lambda: _S("x" * 64))
    r = self_check._check_auth_config()
    assert r.ok and "64자" in r.detail


@pytest.mark.parametrize("key,why", [
    ("", "비어 있음"),
    ("short", "32바이트 미만"),
    ("skin1004-ai-secret-change-me", "공개된 기본값"),
])
def test_invalid_secret_fails_loudly(monkeypatch, key, why):
    from app import config
    monkeypatch.setattr(config, "get_settings", lambda: _S(key))
    r = self_check._check_auth_config()
    assert r.ok is False, why
    assert "로그인" in r.detail          # 무엇이 망가지는지 화면에서 알 수 있어야 한다


def test_secret_value_is_never_logged(monkeypatch):
    """⚠️ 값 자체는 절대 남기지 않는다 — 길이와 판정만."""
    from app import config
    secret = "S3CRET-" + "y" * 60
    monkeypatch.setattr(config, "get_settings", lambda: _S(secret))
    r = self_check._check_auth_config()
    assert secret not in r.detail


def test_check_is_registered_as_critical():
    """로그인 불가는 CRITICAL 이다 — 경고로 두면 묻힌다."""
    ids = {c.id: c for c in self_check.CHECKS}
    assert "auth_config" in ids
    assert ids["auth_config"].severity == self_check.SEV_CRITICAL
