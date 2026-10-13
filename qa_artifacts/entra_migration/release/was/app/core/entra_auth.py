# -*- coding: utf-8 -*-
"""Microsoft Entra ID (OIDC) 로그인 — 사내 계정 통합.

**왜**: 지금은 AD 에서 ID·이름·부서만 가져오고 **비밀번호는 셀라가 자체 저장**한다
(`users.password_hash`, bcrypt). CRM 도 따로 저장한다. 시스템마다 비밀번호가
흩어지는 구조라, 로그인 단계부터 EntraID 로 옮긴다 (2026-09-02 IT 요청).

⚠️ **자격증명이 없으면 이 모듈은 통째로 잠들어 있다** (`is_configured()` 가 False).
   `.env` 에 값이 들어오는 날 켜진다 — 코드 배포 없이.

⛔ **회신 URL 은 `https` 여야 한다. 예외는 `localhost` 뿐이다**
   (learn.microsoft.com/entra/identity-platform/reply-url). 셀라 프로덕션은
   지금 `http://10.1.100.5` (nginx 80번만) 라, HTTPS 가 붙기 전에는 등록 자체가
   안 된다. 그래서 `redirect_uri_is_usable()` 이 **보내기 전에** 막는다 —
   구글에서 겪은 그 실패(사용자는 차단 화면만 보고, 우리 로그엔 아무것도 안 남는다)를
   반복하지 않는다.

⛔ **사용자 식별은 `oid` 로 한다. 이메일로 하지 마라.** 실측(2026-09-01):
   활성 AD 436명 중 **238명은 `mail` 이 비어 있고**, UPN 은 `@cravercorp.com`,
   실제 구글 로그인은 `@skin1004korea.com` 이다. 이메일로 이으면 절반이 실패한다.
   `oid` 는 테넌트 안에서 불변이고 재사용되지 않는다.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import time
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlencode, urlparse

import jwt
import requests
import structlog
from jwt import PyJWKClient

from app.config import get_settings
from app.db.mariadb import execute, fetch_one

logger = structlog.get_logger(__name__)

#: 인가 요청의 왕복 시간. 사람은 바로 누르므로 길게 둘 이유가 없다.
STATE_TTL_SECONDS = 600

#: 신원 확인에 필요한 최소 범위. 메일·파일 접근을 요구하지 않는다.
SCOPE = "openid profile email"

_DISCOVERY_TMPL = "https://login.microsoftonline.com/{tenant}/v2.0/.well-known/openid-configuration"

#: 디스커버리 문서 캐시 — 엔드포인트를 코드에 박지 않는다(박으면 낡는다).
_discovery_cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}
_DISCOVERY_TTL = 3600

_jwk_clients: Dict[str, PyJWKClient] = {}


class EntraUnavailable(Exception):
    """사람이 읽을 수 있는 실패 — 원시 오류는 무엇을 할지 알려 주지 않는다."""

    def __init__(self, title: str, body: str):
        super().__init__(title)
        self.title = title
        self.body = body


_STATE_DDL = """
CREATE TABLE IF NOT EXISTS entra_oidc_states (
    state_hash CHAR(64) PRIMARY KEY,
    nonce_hash CHAR(64) NOT NULL,
    code_verifier VARCHAR(128) NOT NULL,
    redirect_uri VARCHAR(512) NOT NULL,
    next_path VARCHAR(512) NOT NULL DEFAULT '/',
    expires_at DATETIME NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_entra_state_expiry (expires_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""


def ensure_entra_tables() -> None:
    """state 표를 만들고, `users.entra_oid` 를 붙인다 (둘 다 idempotent).

    ⚠️ `entra_oid` 는 **UNIQUE** 여야 한다 — 한 EntraID 계정이 두 셀라 계정에
       연결되면 누가 누구인지 알 수 없게 된다.
    """
    execute(_STATE_DDL)
    execute("DELETE FROM entra_oidc_states WHERE expires_at < UTC_TIMESTAMP()")
    try:
        execute(
            "ALTER TABLE users ADD COLUMN entra_oid VARCHAR(64) NULL "
            "COMMENT 'Entra ID 불변 객체 ID (이메일로 잇지 않는다)'"
        )
    except Exception:
        pass  # 이미 있다
    try:
        execute("ALTER TABLE users ADD UNIQUE INDEX idx_users_entra_oid (entra_oid)")
    except Exception:
        pass  # 이미 있다


# ────────────────────────────── 설정 ──────────────────────────────

def is_configured() -> bool:
    """IT 가 값을 주기 전까지 이 경로는 존재하지 않는 것처럼 동작한다."""
    s = get_settings()
    return bool(getattr(s, "entra_tenant_id", "")
                and getattr(s, "entra_client_id", "")
                and getattr(s, "entra_client_secret", ""))


def redirect_uri_is_usable(redirect_uri: str) -> bool:
    """⛔ EntraID 회신 URL 은 `https` 만 허용된다 (예외: localhost).

    구글은 우리 `http` 주소를 받아 주고 있어 지금까지 문제가 없었지만, Entra 는
    등록 자체가 안 된다. 화면에서 숨기는 것만으로는 부족해 서버도 같은 판정을 한다.
    """
    parsed = urlparse(redirect_uri or "")
    if parsed.scheme == "https":
        return True
    host = (parsed.hostname or "").lower()
    return parsed.scheme == "http" and host in {"localhost", "127.0.0.1", "::1"}


def _discovery() -> Dict[str, Any]:
    """엔드포인트를 **디스커버리 문서에서** 읽는다 — 손으로 적으면 낡는다."""
    tenant = get_settings().entra_tenant_id
    cached = _discovery_cache.get(tenant)
    if cached and (time.time() - cached[0]) < _DISCOVERY_TTL:
        return cached[1]
    try:
        response = requests.get(_DISCOVERY_TMPL.format(tenant=tenant), timeout=15)
        response.raise_for_status()
    except Exception as exc:
        logger.warning("entra_discovery_failed", error_type=type(exc).__name__)
        raise EntraUnavailable(
            "회사 계정 로그인을 사용할 수 없습니다",
            "인증 서버에 연결하지 못했습니다. 잠시 후 다시 시도해 주세요.",
        )
    doc = response.json()
    _discovery_cache[tenant] = (time.time(), doc)
    return doc


# ────────────────────────── state·PKCE (재생·가로채기 방어) ──────────────────────────

def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _pkce_pair() -> Tuple[str, str]:
    """PKCE — 인가 코드가 새더라도 그것만으로는 토큰을 못 받게 한다."""
    verifier = secrets.token_urlsafe(64)[:128]
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).decode().rstrip("=")
    return verifier, challenge


def begin_login(redirect_uri: str, next_path: str = "/") -> str:
    """인가 요청 주소를 만들고, 왕복에 필요한 것을 **서버에** 남긴다.

    ⚠️ state·nonce·code_verifier 를 쿠키가 아니라 서버에 두는 이유: 콜백이 쿠키
       없는 호스트로 돌아오는 사고를 이미 겪었다 (2026-08-25). 서버에 있으면
       그 문제가 구조적으로 없다.
    """
    if not is_configured():
        raise EntraUnavailable(
            "회사 계정 로그인이 아직 설정되지 않았습니다",
            "관리자에게 문의하시거나, 아래에서 기존 방식으로 로그인해 주세요.",
        )
    if not redirect_uri_is_usable(redirect_uri):
        logger.warning("entra_unusable_redirect", redirect_uri=redirect_uri)
        raise EntraUnavailable(
            "이 주소에서는 회사 계정 로그인을 할 수 없습니다",
            "보안 연결(https)이 필요합니다. 관리자에게 문의해 주세요.",
        )

    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    verifier, challenge = _pkce_pair()
    execute(
        "INSERT INTO entra_oidc_states "
        "(state_hash, nonce_hash, code_verifier, redirect_uri, next_path, expires_at) "
        "VALUES (%s, %s, %s, %s, %s, UTC_TIMESTAMP() + INTERVAL %s SECOND)",
        (_sha256(state), _sha256(nonce), verifier, redirect_uri,
         next_path or "/", STATE_TTL_SECONDS),
    )
    settings = get_settings()
    return _discovery()["authorization_endpoint"] + "?" + urlencode({
        "client_id": settings.entra_client_id,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "response_mode": "query",
        "scope": SCOPE,
        "state": state,
        "nonce": nonce,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    })


def consume_state(state: str) -> Dict[str, Any]:
    """state 를 **원자적으로** 소모한다. 재생·위조는 여기서 끝난다."""
    row = fetch_one(
        "SELECT nonce_hash, code_verifier, redirect_uri, next_path FROM entra_oidc_states "
        "WHERE state_hash = %s AND expires_at >= UTC_TIMESTAMP()",
        (_sha256(state or ""),),
    )
    if not row:
        raise EntraUnavailable(
            "로그인 링크가 만료되었습니다",
            "로그인 화면에서 다시 시도해 주세요.",
        )
    changed = execute("DELETE FROM entra_oidc_states WHERE state_hash = %s",
                      (_sha256(state),))
    if changed != 1:
        raise EntraUnavailable("로그인 링크가 만료되었습니다",
                               "로그인 화면에서 다시 시도해 주세요.")
    return row


# ────────────────────────────── 토큰 교환·검증 ──────────────────────────────

def exchange_code(code: str, redirect_uri: str, code_verifier: str) -> Dict[str, Any]:
    settings = get_settings()
    response = requests.post(
        _discovery()["token_endpoint"],
        data={
            "client_id": settings.entra_client_id,
            "client_secret": settings.entra_client_secret,
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "code_verifier": code_verifier,
            "scope": SCOPE,
        },
        timeout=20,
    )
    if response.status_code != 200:
        # ⚠️ 응답 본문에 코드·비밀이 섞일 수 있어 상태 코드만 남긴다.
        logger.warning("entra_token_exchange_failed", status=response.status_code)
        raise EntraUnavailable(
            "회사 계정 확인에 실패했습니다",
            "잠시 후 다시 시도하거나, 관리자에게 문의해 주세요.",
        )
    return response.json()


def verify_id_token(raw_id_token: str, nonce_hash: str) -> Dict[str, Any]:
    """`id_token` 을 **서명부터** 검증한다.

    ⛔ 토큰 응답 본문의 값을 그대로 믿지 않는다. 서명·발급자·대상·만료를 모두 본다.
    ⛔ `tid`(테넌트)를 확인한다 — 다른 테넌트가 발급한 토큰을 받으면 남의 조직
       사용자가 우리 계정으로 들어올 수 있다.
    ⛔ `nonce` 를 확인한다 — 이 로그인이 우리가 시작한 그 왕복인지 잇는다.
    """
    settings = get_settings()
    doc = _discovery()
    jwks_uri = doc["jwks_uri"]
    client = _jwk_clients.get(jwks_uri) or PyJWKClient(jwks_uri)
    _jwk_clients[jwks_uri] = client

    signing_key = client.get_signing_key_from_jwt(raw_id_token)
    claims = jwt.decode(
        raw_id_token,
        signing_key.key,
        algorithms=["RS256"],
        audience=settings.entra_client_id,
        issuer=doc["issuer"].replace("{tenantid}", settings.entra_tenant_id),
    )
    if claims.get("tid") and claims["tid"] != settings.entra_tenant_id:
        raise EntraUnavailable("회사 계정이 아닙니다",
                               "회사 EntraID 계정으로 로그인해 주세요.")
    if _sha256(str(claims.get("nonce", ""))) != nonce_hash:
        raise EntraUnavailable("로그인 확인에 실패했습니다",
                               "로그인 화면에서 다시 시도해 주세요.")
    if not claims.get("oid"):
        raise EntraUnavailable("계정 식별자를 받지 못했습니다",
                               "관리자에게 문의해 주세요.")
    return claims


# ────────────────────────────── 계정 잇기 ──────────────────────────────

def find_user_by_oid(oid: str) -> Optional[dict]:
    """⛔ **이메일이 아니라 `oid`** 로 찾는다 — 이메일은 바뀌고, 절반은 비어 있다."""
    if not oid:
        return None
    return fetch_one(
        "SELECT id, display_name, role, ad_user_id FROM users WHERE entra_oid = %s",
        (oid,),
    )


def link_oid(user_id: int, oid: str) -> None:
    """첫 로그인에서 한 번만 잇는다.

    ⚠️ 이미 다른 사람에게 붙은 `oid` 면 UNIQUE 제약이 막는다 — 조용히 덮어쓰지 않는다.
    """
    execute("UPDATE users SET entra_oid = %s, updated_at = NOW() "
            "WHERE id = %s AND (entra_oid IS NULL OR entra_oid = %s)",
            (oid, int(user_id), oid))


def match_existing_account(claims: Dict[str, Any]) -> Optional[dict]:
    """아직 잇지 않은 사람을 기존 계정에 **한 번** 맞춰 준다.

    ⛔ 이메일 문자열 비교만으로는 안 된다 (활성 AD 436명 중 238명이 `mail` 공백,
       도메인도 `@cravercorp.com` / `@skin1004korea.com` 로 섞여 있다). 그래서
       UPN·이메일의 **로컬파트**로 `ad_users` 를 찾는다 — 실측상 로컬파트는
       조직 전체에서 유일하다(436명 = 436개, 충돌 0건).
    ⛔ 후보가 둘 이상이면 아무것도 고르지 않는다.
    """
    candidates = [claims.get("preferred_username") or "", claims.get("email") or "",
                  claims.get("upn") or ""]
    locals_ = {c.split("@")[0].strip().lower() for c in candidates if "@" in c}
    for local in locals_:
        rows = fetch_one(
            "SELECT u.id, u.display_name, u.role, u.ad_user_id, COUNT(*) OVER () AS n "
            "FROM users u JOIN ad_users a ON u.ad_user_id = a.id "
            "WHERE a.is_active = 1 AND LOWER(SUBSTRING_INDEX(a.email, '@', 1)) = %s",
            (local,),
        )
        if rows and int(rows.get("n", 0)) == 1:
            return rows
        if rows:
            logger.warning("entra_ambiguous_local_part", count=int(rows.get("n", 0)))
    return None
