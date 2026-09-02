# -*- coding: utf-8 -*-
"""Entra ID(OIDC) 로그인 회귀.

**왜 옮기나**: 지금은 AD 에서 ID·이름·부서만 가져오고 비밀번호는 셀라가 자체
저장한다(bcrypt). CRM 도 따로 저장한다 — 시스템마다 비밀번호가 흩어진다.
로그인 단계부터 EntraID 로 통합한다 (2026-09-02 IT 요청).

⚠️ 자격증명이 오기 전이라 **꺼진 상태가 정상**이다. 이 테스트들은 켜지는 날
   지켜져야 할 성질을 미리 고정한다.
"""
from __future__ import annotations

import inspect

import pytest

from app.core import entra_auth as E


class _Settings:
    entra_tenant_id = "tenant-abc"
    entra_client_id = "client-abc"
    entra_client_secret = "secret-abc"
    entra_redirect_uri = "https://ai.example.com/auth/entra/callback"


# ────────────────────────── 켜짐/꺼짐 ──────────────────────────

def test_it_stays_off_until_credentials_arrive(monkeypatch):
    """⚠️ 값이 없으면 기능이 **없는 것처럼** 동작해야 한다 — 고장난 것처럼이 아니라."""
    class _Empty:
        entra_tenant_id = entra_client_id = entra_client_secret = entra_redirect_uri = ""

    monkeypatch.setattr(E, "get_settings", lambda: _Empty())
    assert E.is_configured() is False


def test_it_turns_on_by_configuration_alone(monkeypatch):
    """IT 가 `.env` 에 값을 넣는 날 **코드 배포 없이** 켜진다."""
    monkeypatch.setattr(E, "get_settings", lambda: _Settings())
    assert E.is_configured() is True


def test_starting_a_login_without_credentials_says_so(monkeypatch):
    class _Empty:
        entra_tenant_id = entra_client_id = entra_client_secret = entra_redirect_uri = ""

    monkeypatch.setattr(E, "get_settings", lambda: _Empty())
    with pytest.raises(E.EntraUnavailable):
        E.begin_login("https://ai.example.com/auth/entra/callback")


# ────────────────────────── 회신 URL 규칙 ──────────────────────────

@pytest.mark.parametrize("uri,usable", [
    ("https://ai.cravercorp.com/auth/entra/callback", True),
    ("http://localhost:3000/auth/entra/callback", True),
    ("http://127.0.0.1:3000/auth/entra/callback", True),
    # ⛔ 지금 프로덕션 주소 — Entra 는 등록 자체를 거부한다
    ("http://10.1.100.5/auth/entra/callback", False),
    ("http://ai.cravercorp.internal/auth/entra/callback", False),
])
def test_only_https_or_localhost_reply_urls(uri, usable):
    """⛔ EntraID 회신 URL 은 https 여야 한다 (예외 localhost).
    구글은 우리 http 주소를 받아 줬지만 Entra 는 아니다 — 그래서 HTTPS 가 선결이다."""
    assert E.redirect_uri_is_usable(uri) is usable


def test_an_unusable_reply_url_is_refused_before_leaving_our_server(monkeypatch):
    """⛔ 보내 놓고 실패하면 사용자는 남의 화면에서 막히고 우리 로그엔 아무것도
    안 남는다 — 구글에서 정확히 그 사고를 겪었다 (2026-09-02)."""
    monkeypatch.setattr(E, "get_settings", lambda: _Settings())
    with pytest.raises(E.EntraUnavailable):
        E.begin_login("http://10.1.100.5/auth/entra/callback")


def test_the_callback_url_comes_from_configuration_not_the_host_header():
    """⛔ Host 헤더로 만들면 접속 주소가 여러 개일 때 등록 안 된 값이 나간다.
    Entra 회신 URL 은 정확히 일치해야 하고 대소문자도 구분한다."""
    from app.api import entra_routes

    src = inspect.getsource(entra_routes._redirect_uri)
    assert "entra_redirect_uri" in src
    assert src.index("entra_redirect_uri") < src.index("headers")


# ────────────────────────── 신원 판정 ──────────────────────────

def test_identity_is_keyed_on_the_immutable_object_id_not_email():
    """⛔ 이메일로 이으면 절반이 실패한다 — 실측(2026-09-01): 활성 AD 436명 중
    238명이 `mail` 공백, 도메인도 @cravercorp.com / @skin1004korea.com 로 섞여 있다."""
    src = inspect.getsource(E.find_user_by_oid)
    assert "entra_oid" in src
    assert "email" not in src.split('"""')[-1], "이메일로 찾고 있다"


def test_the_id_token_is_verified_not_trusted():
    """⛔ 토큰 응답 본문을 믿으면 안 된다 — 서명·발급자·대상·만료를 검증한다."""
    src = inspect.getsource(E.verify_id_token)
    for required in ("get_signing_key_from_jwt", "audience", "issuer", "algorithms"):
        assert required in src, f"{required} 검증이 없다"


def test_a_token_from_another_tenant_is_rejected():
    """⛔ `tid` 를 안 보면 다른 조직 사용자가 우리 계정으로 들어올 수 있다."""
    src = inspect.getsource(E.verify_id_token)
    assert '"tid"' in src or "'tid'" in src


def test_the_nonce_ties_the_token_to_our_own_round_trip():
    src = inspect.getsource(E.verify_id_token)
    assert "nonce" in src


def test_the_flow_uses_pkce():
    """인가 코드가 새더라도 그것만으로는 토큰을 못 받게 한다."""
    src = inspect.getsource(E.begin_login)
    assert "code_challenge" in src and "S256" in src


def test_state_is_single_use_and_server_side():
    """⚠️ 쿠키가 아니라 서버에 둔다 — 콜백이 쿠키 없는 호스트로 돌아오는 사고를
    이미 겪었다 (2026-08-25). 서버에 있으면 그 문제가 구조적으로 없다."""
    src = inspect.getsource(E.consume_state)
    assert "DELETE FROM entra_oidc_states" in src
    assert "changed != 1" in src, "재생을 막지 않는다"


# ────────────────────────── 계정 잇기 ──────────────────────────

def test_a_first_login_never_creates_an_account():
    """⛔ 아무나 들어오면 권한(FI 열람 등)의 근거가 사라진다.
    셀라 계정은 AD 기반으로만 만들어진다."""
    from app.api import entra_routes

    src = inspect.getsource(entra_routes.entra_callback)
    assert "INSERT INTO users" not in src
    assert "셀라 계정을 찾지 못했습니다" in src


def test_linking_matches_on_local_part_because_domains_are_mixed():
    """회사 도메인이 둘이고 `mail` 이 절반 비어 있다 — 로컬파트는 조직 전체에서
    유일하다(실측 436명 = 436개, 충돌 0건)."""
    src = inspect.getsource(E.match_existing_account)
    assert "SUBSTRING_INDEX" in src


def test_an_ambiguous_match_links_nothing():
    """⛔ 하나를 고르면 남의 계정에 붙는다 — 모를 때는 멈춘다."""
    src = inspect.getsource(E.match_existing_account)
    assert "!= 1" in src or "== 1" in src
    assert "entra_ambiguous_local_part" in src


def test_one_entra_account_cannot_own_two_cella_accounts():
    """⚠️ UNIQUE 가 없으면 누가 누구인지 알 수 없게 된다."""
    src = inspect.getsource(E.ensure_entra_tables)
    assert "UNIQUE" in src and "entra_oid" in src


def test_linking_does_not_silently_steal_an_existing_link():
    src = inspect.getsource(E.link_oid)
    assert "entra_oid IS NULL" in src


# ────────────────────────── 전환기 공존 ──────────────────────────

def test_local_password_login_still_exists_during_migration():
    """⚠️ EntraID 가 자리를 잡기 전에 기존 경로를 끊으면 전원이 갇힌다."""
    from app.api import auth_api

    assert hasattr(auth_api, "signin")
    assert "password_hash" in inspect.getsource(auth_api.signin)


def test_the_login_screen_asks_the_server_whether_to_show_the_button():
    """⚠️ 프론트에 조건을 박으면 IT 가 값을 준 날 아무도 기억하지 못한다."""
    import io
    import os

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    js = io.open(os.path.join(root, "app", "frontend", "auth.js"), encoding="utf-8").read()
    html = io.open(os.path.join(root, "app", "frontend", "login.html"),
                   encoding="utf-8").read()
    assert "/auth/entra/status" in js
    assert 'id="entra-box"' in html and "hidden" in html


def test_the_login_start_refuses_open_redirects():
    """`next` 로 외부 주소를 넣어 사용자를 끌고 갈 수 없어야 한다."""
    from app.api import entra_routes

    src = inspect.getsource(entra_routes.entra_login)
    assert 'startswith("//")' in src
