# -*- coding: utf-8 -*-
"""구글 계정으로 **본인이** 비밀번호를 되찾는 경로.

관리자 요청(`password_reset.py`)과 나란히 선다. 둘 다 두는 이유는 커버리지다 —
구글을 연결한 사람은 관리자를 기다리지 않고 스스로 끝내고, 안 한 사람은 요청을
남긴다. 하나만 두면 각각 "관리자가 올 때까지 못 들어감" 이거나 "구글 없는 사람은
영영 못 들어감" 이 된다.

⛔ **신원은 우리가 구글에 직접 물어서 받은 이메일로만 판정한다.** 이름·부서는 로그인
   화면이 이미 목록으로 보여 주므로 신원 증거가 될 수 없다 (그래서 관리자 요청
   경로는 계정을 건드리지 않는다). 여기서만 계정을 바꾸는 이유가 그 보증이다.

⛔ **구글 토큰을 저장하지 않는다.** 신원 확인 한 번이면 끝이라 scope 가
   `userinfo.email` 뿐이다 — Gmail·Drive·Calendar 를 요구하지 않는다. 비밀번호를
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
from urllib.parse import urlencode, urlparse

import bcrypt
import jwt
import requests
import structlog

from app.config import get_settings, validate_jwt_secret
from app.db.mariadb import execute, fetch_all, fetch_one

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

# ⛔ **`openid` 를 넣지 마라 — 구글이 정책 위반으로 막는다** (2026-09-01 실사용자 차단).
#    `openid` 가 있으면 OIDC 규칙이 적용돼 리다이렉트가 **https 여야 한다.** 우리 콜백은
#    `http://10.1.100.5.nip.io/...` 라 "액세스 차단됨 · 400 invalid_request ·
#    doesn't comply with Google's OAuth 2.0 policy" 가 뜬다. **계정을 고른 뒤에** 뜨므로
#    URL 만 열어 보는 검사로는 안 잡힌다 (로그인 화면까지는 멀쩡히 나온다).
#    기존 GWS 연결 흐름에는 `openid` 가 없어서 같은 리다이렉트로 잘 동작한다
#    (2026-08-25 실제 연결 성공 기록). 그 모양에 맞춘다 — 엔드포인트도 같은 것을 쓴다.
_AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/auth"
_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
_USERINFO_ENDPOINT = "https://www.googleapis.com/oauth2/v2/userinfo"
_SCOPE = "https://www.googleapis.com/auth/userinfo.email"

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
       구글에 직접 물어 받은 이메일이 한다.
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

def is_available(redirect_uri: str) -> bool:
    """이 서버에서 구글 확인이 **실제로 성립하는지** 본다.

    ⛔ 구글 정책: 리다이렉트 URI 는 **https 여야 하고 예외는 localhost 뿐이다.**
       (https://developers.google.com/identity/protocols/oauth2/policies)
       우리 콜백이 `http://10.1.100.5.nip.io/...` 라, 구글은 계정을 고른 뒤
       "액세스 차단됨 · 400 invalid_request" 로 막는다 — **기존 GWS 연결도
       똑같이 막힌다** (2026-09-01 사용자 확인. 기존 사용자는 토큰이 갱신만 되니
       아무도 몰랐다).

    ⚠️ 그래서 조건이 안 되면 **버튼을 아예 보여 주지 않는다.** 눌러도 막다른 길인
       입구를 두면 "되는 줄 알고 눌렀다가 안 되는" 경험만 남는다 — 관리자 요청
       경로가 그 사람들에게는 유일한 길이고, 그쪽은 지금도 정상이다.

    ⚠️ 판정을 코드가 하므로 **HTTPS 를 켜는 날 저절로 다시 나타난다.** 손으로
       켜고 끄는 플래그를 두면 그날 아무도 기억하지 못한다.
    """
    if not get_settings().google_oauth_client_id:
        return False
    parsed = urlparse(redirect_uri or "")
    if parsed.scheme == "https":
        return True
    host = (parsed.hostname or "").lower()
    return host in {"localhost", "127.0.0.1", "::1"}


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
    """인가 코드를 **구글이 확인해 준** 신원으로 바꾼다.

    ⛔ 신원을 클라이언트가 준 값에서 읽지 않는다. 우리가 직접 구글의 userinfo 를
       TLS 로 호출해 받은 값만 쓴다 — 중간에서 바꿔치기할 자리가 없다.
    ⛔ `verified_email` 이 아니면 신원이 아니다.

    ⚠️ `id_token` 서명 검증이 아니라 userinfo 호출인 이유는 `openid` 를 요구할 수
       없기 때문이다 (위 `_AUTH_ENDPOINT` 주석 참조 — OIDC 는 https 리다이렉트를
       요구하는데 우리 콜백은 http 다). 보증의 세기는 같다: 두 값 모두 구글이
       우리 요청에 직접 답한 것이다.
    """
    settings = get_settings()
    token_response = requests.post(
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
    if token_response.status_code != 200:
        logger.warning("pwreset_google_token_exchange_failed",
                       status=token_response.status_code)
        raise ResetUnavailable(
            "구글 확인에 실패했습니다",
            "잠시 후 다시 시도하거나, 로그인 화면에서 관리자에게 요청을 남겨 주세요.",
        )

    access_token = token_response.json().get("access_token", "")
    if not access_token:
        raise ResetUnavailable(
            "구글 확인에 실패했습니다",
            "구글이 신원 정보를 돌려주지 않았습니다. 관리자에게 요청을 남겨 주세요.",
        )

    info_response = requests.get(
        _USERINFO_ENDPOINT,
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=15,
    )
    if info_response.status_code != 200:
        logger.warning("pwreset_google_userinfo_failed", status=info_response.status_code)
        raise ResetUnavailable(
            "구글 확인에 실패했습니다",
            "잠시 후 다시 시도하거나, 로그인 화면에서 관리자에게 요청을 남겨 주세요.",
        )

    claims = info_response.json()
    if not claims.get("verified_email"):
        raise ResetUnavailable(
            "구글 확인에 실패했습니다",
            "이 구글 계정은 이메일이 확인되지 않은 상태입니다.",
        )
    return str(claims.get("email", "")).strip()


def find_user_by_google_email(email: str) -> Optional[dict]:
    """구글 이메일로 가입 계정을 찾는다 — **두 가지 단서**를 차례로 본다.

    ⚠️ 여기서 "없다" 고 말하는 것은 정보 유출이 아니다 — 이미 그 구글 계정으로
       인증한 사람에게 **자기 계정** 얘기를 하는 것이다. 오히려 말해 주지 않으면
       무엇이 잘못됐는지 알 수 없다.
    """
    if not email:
        return None
    row = fetch_one(
        "SELECT id, display_name, ad_user_id FROM users WHERE LOWER(email) = LOWER(%s)",
        (email,),
    )
    return (row
            or _find_by_linked_google_account(email)
            or _find_by_company_local_part(email))


#: 로컬파트 매칭을 **절대 적용하면 안 되는** 도메인. 개인 메일 주소의 로컬파트가
#: 직원 것과 우연히 겹치는 일은 흔하다 (`hong@gmail.com` vs `hong@cravercorp.com`).
_PUBLIC_MAIL_DOMAINS = frozenset({
    "gmail.com", "googlemail.com", "naver.com", "daum.net", "hanmail.net",
    "kakao.com", "nate.com", "outlook.com", "hotmail.com", "live.com",
    "yahoo.com", "icloud.com", "me.com", "qq.com", "163.com", "proton.me",
})


def company_domains() -> set[str]:
    """회사 메일 도메인을 **AD 에 실제로 있는 값**으로 판정한다.

    ⛔ 손으로 적으면 반드시 낡고, 낡으면 에러가 아니라 조용한 매칭 실패다
       (`{{VALUES:Continent1}}` 를 손으로 적었다가 겪은 것과 같은 부류).
       2026-09-01 실측: skin1004korea.com 112 · cravercorp.com 110 ·
       umma.io 10 · b2link.co.kr 1 — **회사 도메인이 하나가 아니다.**
    """
    rows = fetch_all(
        "SELECT DISTINCT LOWER(SUBSTRING_INDEX(email, '@', -1)) AS d "
        "FROM ad_users WHERE email LIKE %s",
        ("%@%",),
    )
    return {r["d"] for r in rows if r["d"] and r["d"] not in _PUBLIC_MAIL_DOMAINS}


def _find_by_company_local_part(email: str) -> Optional[dict]:
    """회사 도메인이 **둘 이상**이라, 구글이 돌려준 대표 주소와 AD 주소가 다를 수 있다.

    실측(2026-09-01): AD 의 `mail` 이 빈 238명은 `userPrincipalName` 이
    `@cravercorp.com` 인데, 그중 다수가 구글에는 `@skin1004korea.com` 으로
    로그인한다 (연결 기록 4건 중 3건). 별칭이면 구글은 **대표 주소만** 돌려주므로
    문자열 비교로는 영원히 안 맞는다.

    이어 붙일 수 있는 근거도 실측했다:
      · AD 활성 436명의 로컬파트가 436개 — **충돌 0건** (사람을 유일하게 가리킨다)
      · `mail`·UPN 을 둘 다 가진 198명 전원이 **로컬파트가 동일** (다른 경우 0건)

    ⛔ **회사 도메인일 때만 적용한다.** 개인 메일까지 허용하면 `hong@gmail.com`
       하나로 `hong@cravercorp.com` 직원의 비밀번호를 바꿀 수 있다 — 구글 인증은
       통과하므로 방어선이 이 조건뿐이다.
    ⛔ 둘 이상 걸리면 아무것도 고르지 않는다.
    """
    local, _, domain = email.strip().lower().partition("@")
    if not local or not domain or domain not in company_domains():
        return None

    rows = fetch_all(
        "SELECT u.id, u.display_name, u.ad_user_id FROM users u "
        "JOIN ad_users a ON u.ad_user_id = a.id "
        "WHERE a.is_active = 1 AND LOWER(SUBSTRING_INDEX(a.email, '@', 1)) = %s",
        (local,),
    )
    if len(rows) == 1:
        return rows[0]
    if rows:
        logger.warning("pwreset_google_ambiguous_local_part", count=len(rows))
    return None


def _find_by_linked_google_account(email: str) -> Optional[dict]:
    """앱에서 **로그인한 상태로 직접 연결해 둔** 구글 계정으로 찾는다.

    ⛔ `users.email` 만 보면 절반이 영영 이 경로를 못 쓴다. AD 에 `mail` 속성이
       없는 계정이 많아(2026-09-01 실측: 활성 436명 중 **231명**) 가입 시
       `ad_<id>@noemail.local` 폴백이 박히기 때문이다. 그 사람들도 회사 구글
       계정은 있고, 실제로 앱에서 연결까지 해 둔 사람이 있다 — 폴백값 18명 중
       4명이 그렇다. 이메일 컬럼만 보면 그들을 관리자 경로에만 묶어 둔다.

    연결 기록은 **로그인한 상태에서** 본인이 만든 것이므로 `users.email` 과 같은
    세기의 증거다 (추측이 아니라 그때 인증된 결속이다).

    ⛔ 두 계정이 같은 구글 계정을 연결했다면 **아무것도 고르지 않는다.** 하나를
       고르면 남의 계정 비밀번호를 바꿔 주게 된다 — 모를 때는 멈추는 쪽이다.
    """
    from app.core.google_auth import GoogleAuthManager

    manager = GoogleAuthManager()
    wanted = email.strip().casefold()
    matches = []
    for row in fetch_all("SELECT id, display_name, ad_user_id, email FROM users"):
        stored = manager.get_stored_google_email(row.get("email") or "")
        if stored and stored.strip().casefold() == wanted:
            matches.append(row)

    if len(matches) == 1:
        return matches[0]
    if matches:
        # 사람이 봐야 할 상태다 — 조용히 없는 셈 치면 원인을 영영 모른다.
        logger.warning("pwreset_google_ambiguous_link", count=len(matches))
    return None


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
