# -*- coding: utf-8 -*-
"""구글 본인 확인으로 비밀번호를 되찾는 경로 회귀.

관리자 요청 경로(`test_password_reset_request.py`)와 **둘 다** 있어야 한다.
구글을 연결한 사람은 기다리지 않고 끝내고, 못 쓰는 사람은 요청을 남긴다.

⛔ 이 경로는 계정을 실제로 바꾼다. 그래도 되는 이유는 신원을 **구글이 서명으로**
   보증하기 때문이다 — 부서·이름은 로그인 화면이 이미 목록으로 보여 주므로
   신원 증거가 못 된다. 그 구분이 무너지면 두 경로를 나눈 의미가 없어진다.
"""
from __future__ import annotations

import inspect
from datetime import datetime, timedelta, timezone

import pytest

from app.core import password_reset_google as pwg


class _FakeDB:
    """state·grant 표를 대신하는 최소 저장소 (DB 없이 규칙만 검사한다)."""

    def __init__(self):
        self.states: set[str] = set()
        self.grants: dict[str, int] = {}

    def fetch_one(self, sql, params=()):
        if "PASSWORD_RESET_GRANTS" in sql.upper():
            found = self.grants.get(params[0])
            return {"user_id": found} if found is not None else None
        return None


@pytest.fixture
def db(monkeypatch):
    fake = _FakeDB()

    def execute(sql, params=()):
        upper = " ".join(sql.split()).upper()
        if upper.startswith("CREATE"):
            return 0
        if upper.startswith("DELETE FROM PASSWORD_RESET_OAUTH_STATES WHERE NONCE_HASH"):
            return 1 if params[0] in fake.states and not fake.states.discard(params[0]) else 0
        if upper.startswith("DELETE FROM PASSWORD_RESET_OAUTH_STATES"):
            return 0  # 만료 청소 — 여기서는 아무것도 지우지 않는다
        if upper.startswith("DELETE FROM PASSWORD_RESET_GRANTS WHERE CODE_HASH"):
            return 1 if fake.grants.pop(params[0], None) is not None else 0
        if upper.startswith("DELETE FROM PASSWORD_RESET_GRANTS"):
            return 0
        if upper.startswith("INSERT INTO PASSWORD_RESET_OAUTH_STATES"):
            fake.states.add(params[0])
            return 1
        if upper.startswith("INSERT INTO PASSWORD_RESET_GRANTS"):
            fake.grants[params[0]] = params[1]
            return 1
        return 0

    monkeypatch.setattr(pwg, "execute", execute)
    monkeypatch.setattr(pwg, "fetch_one", fake.fetch_one)
    return fake


# ────────────────────────── 공용 콜백을 가르는 판정 ──────────────────────────

def test_a_normal_google_connect_state_is_not_taken_for_a_reset(db):
    """⛔ 가장 위험한 혼선이다 — 재설정 분기는 **로그인 요구를 건너뛴다.**

    일반 GWS 연결 state 가 이 분기로 새면 로그인하지 않은 사람이 그 흐름에 들어온다.
    """
    from app.core import google_oauth_state

    gws_token = None
    try:
        gws_token = google_oauth_state.jwt.encode(
            {"purpose": google_oauth_state.PURPOSE, "user_id": 1, "email": "a@b.c",
             "nonce": "x", "iat": datetime.now(timezone.utc),
             "exp": datetime.now(timezone.utc) + timedelta(minutes=5)},
            pwg._secret(), algorithm="HS256")
    except Exception:  # pragma: no cover - 서명 재료가 없으면 검사 자체가 불가
        pytest.skip("jwt secret unavailable")

    assert pwg.is_reset_state(gws_token) is False


@pytest.mark.parametrize("token", ["", "not-a-jwt", "a.b.c"])
def test_garbage_is_never_a_reset_state(token):
    assert pwg.is_reset_state(token) is False


def test_a_state_signed_with_another_secret_is_rejected(db):
    """⛔ 서명을 안 보고 purpose 만 읽으면 누구나 이 분기로 들어올 수 있다."""
    import jwt as _jwt

    forged = _jwt.encode(
        {"purpose": pwg.PURPOSE, "nonce": "n",
         "exp": datetime.now(timezone.utc) + timedelta(minutes=5)},
        "some-other-secret", algorithm="HS256")
    assert pwg.is_reset_state(forged) is False


def test_the_reset_branch_is_decided_before_the_login_gate():
    """분기가 로그인 검사 **뒤**에 있으면 영영 닿지 않는다 (재설정하려는 사람은
    로그인이 안 되는 사람이다)."""
    from app.api import auth_routes

    src = inspect.getsource(auth_routes.google_callback)
    assert "is_reset_state" in src, "공용 콜백이 재설정 흐름을 가르지 않는다"
    assert src.index("is_reset_state") < src.index("if user is None"), \
        "재설정 분기가 로그인 검사 뒤에 있다 — 닿지 않는다"


# ────────────────────────── 일회용 보장 ──────────────────────────

def test_state_is_single_use(db):
    token = pwg.issue_state()
    assert pwg.is_reset_state(token) is True
    pwg.consume_state(token)
    with pytest.raises(ValueError):
        pwg.consume_state(token)


def test_an_expired_state_is_refused(db):
    past = datetime.now(timezone.utc) - pwg.STATE_TTL - timedelta(minutes=1)
    token = pwg.issue_state(now=past)
    with pytest.raises(Exception):
        pwg.consume_state(token, now=datetime.now(timezone.utc))


def test_grant_is_single_use(db):
    code = pwg.issue_grant(42)
    assert pwg.peek_grant(code) == 42
    assert pwg.consume_grant(code) == 42
    with pytest.raises(ValueError):
        pwg.consume_grant(code)


def test_peeking_does_not_spend_the_grant(db):
    """⚠️ 착륙 지점이 증표를 확인만 하고 태우면 안 된다 — 태우면 새 비밀번호를
    정하기도 전에 만료된다."""
    code = pwg.issue_grant(7)
    assert pwg.peek_grant(code) == 7
    assert pwg.peek_grant(code) == 7
    assert pwg.consume_grant(code) == 7


# ────────────────────────── 최소 권한 ──────────────────────────

def test_identity_check_asks_for_nothing_but_identity(monkeypatch, db):
    """⛔ 비밀번호를 찾으려는 사람에게 메일·드라이브 열람 동의를 물으면 안 된다."""
    class _S:
        google_oauth_client_id = "cid.apps.googleusercontent.com"
        google_oauth_client_secret = "secret"

    monkeypatch.setattr(pwg, "get_settings", lambda: _S())
    url = pwg.build_auth_url("http://host/auth/google/callback", "STATE")

    for privileged in ("gmail", "drive", "calendar"):
        assert privileged not in url, f"신원 확인에 {privileged} 권한을 요구한다"
    assert "scope=openid+email" in url or "scope=openid%20email" in url
    assert "state=STATE" in url
    # 회사 계정과 개인 계정이 함께 로그인돼 있으면 말없이 개인 계정으로 확인된다.
    assert "prompt=select_account" in url


def test_the_module_never_stores_google_tokens():
    """신원 확인 한 번이면 끝이다 — 토큰을 남기면 지켜야 할 것이 하나 더 는다."""
    src = inspect.getsource(pwg)
    for forbidden in ("refresh_token", "save_credentials", "token_path"):
        assert forbidden not in src, f"구글 토큰을 다룬다: {forbidden}"


def test_identity_comes_from_a_verified_signature_not_the_token_response():
    """⛔ 토큰 응답 본문의 이메일은 신원이 아니다 — id_token 서명을 검증해야 한다."""
    src = inspect.getsource(pwg.verified_google_email)
    assert "verify_oauth2_token" in src
    assert "email_verified" in src, "확인되지 않은 이메일을 신원으로 받는다"


# ────────────────────────── 비밀번호 설정 ──────────────────────────

def test_self_chosen_password_does_not_force_another_change():
    """관리자 임시 비밀번호와 다르다 — 본인이 방금 고른 값이라 또 바꾸라고 할 이유가 없다.
    반대로 서 있던 강제는 내려야 한다 (안 내리면 로그인 후 계속 막힌다)."""
    src = inspect.getsource(pwg.set_password)
    assert "must_change_password = 0" in src
    assert "must_change_password = 1" not in src


def test_completing_the_reset_also_closes_the_admin_request():
    """⛔ 대기열에 남으면 관리자가 임시 비밀번호를 발급하고, 그 순간 방금 정한
    비밀번호가 무효가 된다."""
    from app.api import auth_api

    src = inspect.getsource(auth_api.password_reset_google_complete)
    assert "close_for_ad_user" in src


def test_a_too_short_password_does_not_burn_the_grant():
    """길이 검사가 증표 소모 뒤에 있으면, 짧게 한 번 눌렀다가 처음부터 다시 해야 한다."""
    from app.api import auth_api

    src = inspect.getsource(auth_api.password_reset_google_complete)
    assert src.index("MIN_PASSWORD_LEN") < src.index("consume_grant")


def test_the_grant_never_stays_in_the_address_bar():
    """⚠️ 증표가 URL 에 남으면 방문기록·리퍼러·화면 공유에 그대로 실린다."""
    from app.api import auth_api

    src = inspect.getsource(auth_api.password_reset_google_land)
    assert "set_cookie" in src and "httponly=True" in src.replace(" ", "")
    assert "/login?reset=1" in src, "깨끗한 주소로 되돌리지 않는다"


def test_the_cookie_does_not_leak_onto_the_admin_request_endpoint():
    """쿠키 경로가 `/api/auth/password-reset` 이라 `-request` 엔드포인트에는 붙지 않는다
    (경로 접두 뒤가 `/` 여야 일치한다)."""
    assert pwg.GRANT_COOKIE_PATH == "/api/auth/password-reset"


# ────────────────────────── 누가 이 경로를 쓸 수 있나 ──────────────────────────
# ⛔ `users.email` 만 보면 절반이 영영 못 쓴다 — AD 에 `mail` 속성이 없는 계정이
#    많아(실측 436명 중 231명) 가입 시 `ad_<id>@noemail.local` 폴백이 박힌다.

def _stub_lookup(monkeypatch, users, linked):
    """users 표와 '연결해 둔 구글 계정' 을 대신한다."""
    def fetch_one(sql, params=()):
        wanted = str(params[0]).casefold()
        for u in users:
            if (u.get("email") or "").casefold() == wanted:
                return u
        return None

    class FakeManager:
        def get_stored_google_email(self, user_email):
            return linked.get(user_email, "")

    monkeypatch.setattr(pwg, "fetch_one", fetch_one)
    monkeypatch.setattr(pwg, "fetch_all", lambda sql, params=(): users)
    monkeypatch.setattr(pwg, "_find_by_company_local_part", lambda email: None)
    import app.core.google_auth as ga
    monkeypatch.setattr(ga, "GoogleAuthManager", FakeManager)


def test_the_email_column_is_tried_first(monkeypatch):
    users = [{"id": 1, "display_name": "본인", "ad_user_id": 11,
              "email": "me@skin1004korea.com"}]
    _stub_lookup(monkeypatch, users, linked={})

    assert pwg.find_user_by_google_email("ME@skin1004korea.com")["id"] == 1


def test_a_placeholder_email_still_reaches_its_owner_through_the_linked_account(monkeypatch):
    """AD 에 mail 이 없어 폴백이 박힌 사람도, 앱에서 구글을 연결해 뒀다면 쓸 수 있어야 한다.

    그 연결은 **로그인한 상태로** 본인이 만든 것이라 `users.email` 과 같은 세기의
    증거다 — 추측이 아니라 그때 인증된 결속이다.
    """
    users = [{"id": 7, "display_name": "폴백", "ad_user_id": 77,
              "email": "ad_77@noemail.local"}]
    _stub_lookup(monkeypatch, users,
                 linked={"ad_77@noemail.local": "real@skin1004korea.com"})

    found = pwg.find_user_by_google_email("real@skin1004korea.com")
    assert found and found["id"] == 7


def test_two_accounts_linked_to_one_google_account_pick_nothing(monkeypatch):
    """⛔ 하나를 고르면 남의 계정 비밀번호를 바꿔 준다 — 모를 때는 멈춘다."""
    users = [
        {"id": 1, "display_name": "A", "ad_user_id": 11, "email": "ad_11@noemail.local"},
        {"id": 2, "display_name": "B", "ad_user_id": 22, "email": "ad_22@noemail.local"},
    ]
    _stub_lookup(monkeypatch, users, linked={
        "ad_11@noemail.local": "shared@skin1004korea.com",
        "ad_22@noemail.local": "shared@skin1004korea.com",
    })

    assert pwg.find_user_by_google_email("shared@skin1004korea.com") is None


def test_no_link_and_no_matching_email_means_the_admin_path(monkeypatch):
    users = [{"id": 3, "display_name": "C", "ad_user_id": 33, "email": "ad_33@noemail.local"}]
    _stub_lookup(monkeypatch, users, linked={})

    assert pwg.find_user_by_google_email("stranger@gmail.com") is None


# ─────────────── 회사 도메인이 둘이라 문자열 비교로는 안 맞는다 ───────────────
# 실측: AD 의 mail 이 빈 238명은 UPN 이 @cravercorp.com 인데, 구글에는 다수가
# @skin1004korea.com 으로 로그인한다. 별칭이면 구글은 대표 주소만 돌려준다.

def _stub_local_part(monkeypatch, ad_rows, domains=("cravercorp.com", "skin1004korea.com")):
    monkeypatch.setattr(pwg, "fetch_one", lambda sql, params=(): None)
    monkeypatch.setattr(pwg, "_find_by_linked_google_account", lambda email: None)
    monkeypatch.setattr(pwg, "company_domains", lambda: set(domains))
    monkeypatch.setattr(pwg, "fetch_all", lambda sql, params=(): [
        r for r in ad_rows if r["_local"] == params[0]
    ])


def test_a_different_company_domain_still_finds_the_same_person(monkeypatch):
    """AD 는 `hong@cravercorp.com`, 구글은 `hong@skin1004korea.com` — 같은 사람이다.
    로컬파트는 조직 전체에서 유일하다(실측 436/436, 충돌 0)."""
    _stub_local_part(monkeypatch, [
        {"id": 5, "display_name": "홍", "ad_user_id": 55, "_local": "hong"},
    ])

    found = pwg.find_user_by_google_email("hong@skin1004korea.com")
    assert found and found["id"] == 5


def test_a_personal_account_can_never_borrow_an_employee_local_part(monkeypatch):
    """⛔ 이 조건이 유일한 방어선이다 — 구글 인증 자체는 통과하기 때문이다.
    `hong@gmail.com` 으로 `hong@cravercorp.com` 직원 비밀번호를 바꿀 수 있으면 끝이다."""
    _stub_local_part(monkeypatch, [
        {"id": 5, "display_name": "홍", "ad_user_id": 55, "_local": "hong"},
    ])

    assert pwg.find_user_by_google_email("hong@gmail.com") is None


@pytest.mark.parametrize("public", ["gmail.com", "naver.com", "outlook.com", "kakao.com"])
def test_public_providers_are_never_company_domains(public):
    """AD 에 개인 메일이 한 건 섞여 들어와도 구멍이 되지 않게 한다."""
    assert public in pwg._PUBLIC_MAIL_DOMAINS


def test_company_domains_are_read_from_ad_not_hardcoded():
    """⛔ 손으로 적으면 낡고, 낡으면 에러가 아니라 조용한 매칭 실패다.
    회사 도메인은 하나가 아니다 (실측 4개)."""
    src = inspect.getsource(pwg.company_domains)
    assert "ad_users" in src, "AD 에서 읽지 않는다"
    # docstring 의 실측 근거는 남겨도 된다 — 막을 것은 **로직**의 리터럴이다.
    body = src.split('"""')[-1]
    for hardcoded in ("skin1004korea.com", "cravercorp.com"):
        assert hardcoded not in body, "회사 도메인을 로직에 박았다"


def test_two_people_sharing_a_local_part_pick_nothing(monkeypatch):
    _stub_local_part(monkeypatch, [
        {"id": 5, "display_name": "A", "ad_user_id": 55, "_local": "hong"},
        {"id": 6, "display_name": "B", "ad_user_id": 66, "_local": "hong"},
    ])

    assert pwg.find_user_by_google_email("hong@cravercorp.com") is None


# ────────────────────────── 화면 ──────────────────────────

def _login_html() -> str:
    import io
    import os

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return io.open(os.path.join(root, "app", "frontend", "login.html"),
                   encoding="utf-8").read()


def test_the_login_screen_offers_both_recovery_paths():
    """둘 다 있어야 한다 — 하나만 두면 '관리자를 기다려야만' 이거나
    '구글 없는 사람은 영영 못 들어감' 이 된다."""
    html = _login_html()
    assert 'id="forgot-google"' in html, "구글 본인 확인 경로가 없다"
    assert "/api/auth/password-reset/google/start" in html
    assert 'id="forgot-submit"' in html, "관리자 요청 경로가 없다"
    assert 'id="reset-box"' in html, "새 비밀번호를 정할 자리가 없다"


def test_the_new_password_form_does_not_carry_the_grant():
    """증표는 HttpOnly 쿠키에 있다 — 화면이 값을 들고 있으면 안 된다."""
    html = _login_html()
    assert "?rc=" not in html, "증표가 화면 주소에 실려 있다"
    assert 'name="rc"' not in html and 'id="rc"' not in html, "화면이 증표를 담고 있다"
