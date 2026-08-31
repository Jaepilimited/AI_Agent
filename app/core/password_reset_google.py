# -*- coding: utf-8 -*-
"""구글 계정으로 **본인이** 비밀번호를 되찾는 경로.

관리자 요청(`password_reset.py`)과 나란히 선다. 둘 다 두는 이유는 커버리지다 —
구글을 연결한 사람은 관리자를 기다리지 않고 스스로 끝내고, 안 한 사람은 요청을
남긴다. 하나만 두면 각각 "관리자가 올 때까지 못 들어감" 이거나 "구글 없는 사람은
영영 못 들어감" 이 된다.

⛔ **신원은 구글이 서명한 `id_token` 의 이메일로만 판정한다.** 이름·부서는 로그인
   화면이 이미 목록으로 보여 주므로 신원 증거가 될 수 없다 (그래서 관리자 요청
   경로는 계정을 건드리지 않는다). 여기서만 계정을 바꾸는 이유가 그 서명이다.

⛔ **구글 토큰을 저장하지 않는다.** 신원 확인 한 번이면 끝이라 scope 가
   `openid email` 뿐이다 — Gmail·Drive·Calendar 를 요구하지 않는다. 비밀번호를
   찾으려는 사람에게 메일 열람 동의를 물으면 그것 자체가 이상한 화면이다.

⚠️ **리다이렉트 URI 는 새로 만들 수 없다.** 구글 콘솔에 등록된 것만 쓸 수 있어
   기존 `/auth/google/callback` 을 그대로 쓰고, state 의 purpose 로 갈라낸다.

⚠️ 이 경로는 **쿠키가 필요 없다** — state 가 서버에 있어서다. 콜백이 쿠키 없는
   호스트(`…nip.io`)로 돌아오는 그 사고(2026-08-25)를 구조적으로 비켜간다.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urlencode

import bcrypt
import jwt
import requests
import structlog
from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2 import id_token as google_id_token

from app.config import get_settings, validate_jwt_secret
from app.db.mariadb import execute, fetch_one

logger = structlog.get_logger(__name__)

#: state JWT 의 용도. 공용 콜백에서 이 값으로 일반 연결 흐름과 갈라진다.
PURPOSE = "pw_reset_oauth"

#: 구글 왕복에 주는 시간. 길게 둘 이유가 없다 — 사람은 바로 누른다.
STATE_TTL = timedelta(minutes=10)

#: 신원 확인이 끝난 뒤 새 비밀번호를 정할 때까지의 시간.
GRANT_TTL = timedelta(minutes=5)

#: 확인 증표를 담는 쿠키. 주소창·방문기록·리퍼러에 남지 않게 URL 에서 옮겨 담는다.
GRANT_COOKIE = "cella_pw_reset"
GRANT_COOKIE_PATH = "/api/auth/password-reset"

_AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
_SCOPE = "openid email"

MIN_PASSWORD_LEN = 4


class ResetUnavailable(Exception):
    """사람이 읽을 수 있는 실패 — 원시 401 JSON 은 무엇을 할지 알려 주지 않는다."""

    def __init__(self, title: str, body: str):
        super().__init__(title)
        self.title = title
        self.body = body


_STATE_DDL = """
CREATE TABLE IF NOT EXISTS password_reset_oauth_states (
    nonce_hash CHAR(64) PRIMARY KEY,
    expires_at DATETIME NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_pwreset_state_expiry (expires_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""

_GRANT_DDL = """
CREATE TABLE IF NOT EXISTS password_reset_grants (
    code_hash CHAR(64) PRIMARY KEY,
    user_id INT NOT NULL,
    expires_at DATETIME NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_pwreset_grant_expiry (expires_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""


def ensure_google_reset_tables() -> None:
    """표를 만들고, 더는 재생 방어에 쓸모없는 행을 지운다."""
    execute(_STATE_DDL)
    execute(_GRANT_DDL)
    _cleanup()


def _cleanup() -> None:
    execute("DELETE FROM password_reset_oauth_states WHERE expires_at < UTC_TIMESTAMP()")
    execute("DELETE FROM password_reset_grants WHERE expires_at < UTC_TIMESTAMP()")


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _secret() -> str:
    return validate_jwt_secret(get_settings().jwt_secret_key)


# ────────────────────────────── state (CSRF·재생 방어) ──────────────────────────────

def issue_state(now: Optional[datetime] = None) -> str:
    """서명된 state 를 발급하고 그 일회용 nonce 를 서버에 남긴다.

    ⚠️ 로그인 전 경로라 사용자에 묶을 수 없다 — 묶을 신원이 아직 없다.
       그래서 이 state 는 "이 왕복을 우리가 시작했다" 만 보증한다. 신원 보증은
       구글이 서명한 `id_token` 이 한다.
    """
    _cleanup()
    current = now or datetime.now(timezone.utc)
    nonce = secrets.token_urlsafe(32)
    expires = current + STATE_TTL
    execute(
        "INSERT INTO password_reset_oauth_states (nonce_hash, expires_at) VALUES (%s, %s)",
        (_sha256(nonce), expires.replace(tzinfo=None)),
    )
    return jwt.encode(
        {"purpose": PURPOSE, "nonce": nonce, "iat": current, "exp": expires},
        _secret(),
        algorithm="HS256",
    )


def is_reset_state(token: str) -> bool:
    """공용 콜백이 이 흐름인지 가려낸다. **서명을 검증한 뒤에만** 참이다.

    ⛔ 서명 없이 purpose 만 읽으면 누구나 이 분기로 들어올 수 있다 —
       그 분기는 로그인을 요구하지 않는 쪽이다.
    """
    if not token:
        return False
    try:
        payload = jwt.decode(token, _secret(), algorithms=["HS256"])
    except Exception:
        return False
    return payload.get("purpose") == PURPOSE


def consume_state(token: str, now: Optional[datetime] = None) -> None:
    """state 를 검증하고 **원자적으로** 소모한다. 실패하면 ValueError."""
    payload = jwt.decode(
        token, _secret(), algorithms=["HS256"], options={"verify_exp": now is None}
    )
    if payload.get("purpose") != PURPOSE:
        raise ValueError("wrong state purpose")
    if now is not None and datetime.fromtimestamp(payload["exp"], timezone.utc) < now:
        raise ValueError("expired state")
    changed = execute(
        "DELETE FROM password_reset_oauth_states "
        "WHERE nonce_hash = %s AND expires_at >= UTC_TIMESTAMP()",
        (_sha256(str(payload["nonce"])),),
    )
    if changed != 1:
        raise ValueError("state already used or expired")


# ────────────────────────────── grant (확인 완료 증표) ──────────────────────────────

def issue_grant(user_id: int, now: Optional[datetime] = None) -> str:
    """신원 확인이 끝났다는 **일회용** 증표를 발급한다."""
    current = now or datetime.now(timezone.utc)
    code = secrets.token_urlsafe(32)
    execute(
        "INSERT INTO password_reset_grants (code_hash, user_id, expires_at) VALUES (%s, %s, %s)",
        (_sha256(code), int(user_id), (current + GRANT_TTL).replace(tzinfo=None)),
    )
    return code


def peek_grant(code: str) -> Optional[int]:
    """증표가 아직 살아 있는지만 본다 (소모하지 않는다)."""
    if not code:
        return None
    row = fetch_one(
        "SELECT user_id FROM password_reset_grants "
        "WHERE code_hash = %s AND expires_at >= UTC_TIMESTAMP()",
        (_sha256(code),),
    )
    return int(row["user_id"]) if row else None


def consume_grant(code: str) -> int:
    """증표를 원자적으로 소모하고 대상 user_id 를 돌려준다.

    ⛔ 조회하고 나중에 지우면 두 번 쓸 수 있다 — DELETE 의 영향 행 수로 판정한다.
    """
    user_id = peek_grant(code)
    if user_id is None:
        raise ValueError("grant not found or expired")
    changed = execute(
        "DELETE FROM password_reset_grants "
        "WHERE code_hash = %s AND expires_at >= UTC_TIMESTAMP()",
        (_sha256(code),),
    )
    if changed != 1:
        raise ValueError("grant already used")
    return user_id


# ────────────────────────────── 구글 왕복 ──────────────────────────────

def build_auth_url(redirect_uri: str, state: str) -> str:
    """구글 동의 화면 주소. scope 는 신원 확인에 필요한 최소치뿐이다."""
    settings = get_settings()
    if not settings.google_oauth_client_id:
        raise ResetUnavailable(
            "구글 확인을 쓸 수 없습니다",
            "이 서버에 구글 로그인이 설정돼 있지 않습니다. 관리자에게 요청을 남겨 주세요.",
        )
    return _AUTH_ENDPOINT + "?" + urlencode({
        "client_id": settings.google_oauth_client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": _SCOPE,
        "state": state,
        # ⚠️ 계정을 고르게 한다 — 회사 계정과 개인 계정이 함께 로그인돼 있으면
        #    말없이 개인 계정으로 확인되고 "계정을 찾지 못했다" 로 끝난다.
        "prompt": "select_account",
    })


def verified_google_email(code: str, redirect_uri: str) -> str:
    """인가 코드를 구글이 서명한 신원으로 바꾼다.

    ⛔ 토큰 응답의 이메일을 그냥 믿지 않고 `id_token` 서명을 검증한다.
    ⛔ 확인되지 않은 이메일(`email_verified=false`)은 신원이 아니다.
    """
    settings = get_settings()
    response = requests.post(
        _TOKEN_ENDPOINT,
        data={
            "code": code,
            "client_id": settings.google_oauth_client_id,
            "client_secret": settings.google_oauth_client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        },
        timeout=15,
    )
    if response.status_code != 200:
        logger.warning("pwreset_google_token_exchange_failed", status=response.status_code)
        raise ResetUnavailable(
            "구글 확인에 실패했습니다",
            "잠시 후 다시 시도하거나, 관리자에게 요청을 남겨 주세요.",
        )

    raw_id_token = response.json().get("id_token", "")
    if not raw_id_token:
        raise ResetUnavailable(
            "구글 확인에 실패했습니다",
            "구글이 신원 정보를 돌려주지 않았습니다. 관리자에게 요청을 남겨 주세요.",
        )

    claims = google_id_token.verify_oauth2_token(
        raw_id_token, GoogleRequest(), settings.google_oauth_client_id
    )
    if not claims.get("email_verified"):
        raise ResetUnavailable(
            "구글 확인에 실패했습니다",
            "이 구글 계정은 이메일이 확인되지 않은 상태입니다.",
        )
    return str(claims.get("email", "")).strip()


def find_user_by_google_email(email: str) -> Optional[dict]:
    """구글 이메일로 가입 계정을 찾는다.

    ⚠️ 여기서 "없다" 고 말하는 것은 정보 유출이 아니다 — 이미 그 구글 계정으로
       인증한 사람에게 **자기 계정** 얘기를 하는 것이다. 오히려 말해 주지 않으면
       무엇이 잘못됐는지 알 수 없다.
    """
    if not email:
        return None
    return fetch_one(
        "SELECT id, display_name, ad_user_id FROM users WHERE LOWER(email) = LOWER(%s)",
        (email,),
    )


# ────────────────────────────── 비밀번호 설정 ──────────────────────────────

def set_password(user_id: int, new_password: str) -> None:
    """본인이 정한 비밀번호를 저장한다.

    ⛔ `must_change_password` 를 **세우지 않는다** — 관리자가 임시 비밀번호를
       발급한 경우와 달리, 이건 본인이 방금 고른 값이다. 다시 바꾸라고 하면
       이유 없는 관문이 하나 더 생긴다. 반대로 서 있던 강제는 **내린다**.
    """
    if len(new_password) < MIN_PASSWORD_LEN:
        raise ValueError(f"새 비밀번호는 {MIN_PASSWORD_LEN}자 이상이어야 합니다")
    pw_hash = bcrypt.hashpw(new_password.encode(), bcrypt.gensalt()).decode()
    execute(
        "UPDATE users SET password_hash = %s, must_change_password = 0, updated_at = NOW() "
        "WHERE id = %s",
        (pw_hash, int(user_id)),
    )
