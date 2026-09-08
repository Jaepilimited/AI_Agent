# -*- coding: utf-8 -*-
"""로컬 ID/PW 로그인 차단 회귀 — **스위치이지 삭제가 아니다** (2026-09-08).

회사 계정(Entra ID)으로 옮기면서 아이디·비밀번호 경로를 닫았다. 다만 85명 중
회사 계정을 연결한 사람이 아직 1명이라, **못 들어오는 사람이 나올 가능성이
살아 있다.** 그래서 코드를 지우지 않고 설정 하나로 껐다:

    app/config.py  ·  password_login_enabled: bool = False
    되살리기       ·  WAS 의 `.env` 에 PASSWORD_LOGIN_ENABLED=true → 재기동

이 파일이 지키는 것은 두 방향이다:
  ① 꺼져 있으면 비밀번호를 받거나 발급하는 길이 **하나도 남지 않는다**
  ② 켜면 **예전 그대로 돌아온다** (되돌릴 수 없는 삭제가 아니다)

⛔ 화면 쪽 규칙이 하나 더 있다: 서버에 물어보지 못했을 때는 폼을 **숨기지
   않는다.** 여기서 닫는 쪽으로 실패하면 아무도 로그인할 수 없다.
"""
from __future__ import annotations

import inspect
from pathlib import Path

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.api import admin_group_api, auth_api, auth_routes
from app.api.auth_middleware import get_current_user, get_optional_user
from app.db.models import User

_ROOT = Path(__file__).resolve().parents[1]


class _Cfg:
    """`require_password_login()` 이 보는 것은 이 필드 하나다."""

    def __init__(self, enabled: bool):
        self.password_login_enabled = enabled


@pytest.fixture
def off(monkeypatch):
    monkeypatch.setattr(auth_api, "get_settings", lambda: _Cfg(False))


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setattr(auth_api, "get_settings", lambda: _Cfg(True))


def _auth_client() -> TestClient:
    app = FastAPI()
    app.include_router(auth_api.auth_api_router)
    return TestClient(app, raise_server_exceptions=False)


def _admin_client() -> TestClient:
    app = FastAPI()
    app.include_router(admin_group_api.ad_router)

    async def _admin():
        return User(id=1, email="admin@example.com", role="admin")

    app.dependency_overrides[get_current_user] = _admin
    return TestClient(app, raise_server_exceptions=False)


def _reset_callback_client() -> TestClient:
    app = FastAPI()
    app.include_router(auth_routes.auth_router)

    async def _nobody():
        return None

    # ⚠️ 재설정 분기는 **로그인 없이** 들어온다 — 로그인 못 하는 사람의 경로다.
    app.dependency_overrides[get_optional_user] = _nobody
    return TestClient(app, raise_server_exceptions=False)


_CREDS = {"department": "D", "name": "홍길동", "password": "secret123"}


def _says_use_the_company_account(response) -> None:
    """⛔ 맨 에러로 막지 마라 — 무엇을 하면 되는지 화면에 적혀 있어야 한다."""
    assert response.status_code == 403, response.text
    assert "회사 계정" in response.text


# ────────────────────── ① 꺼져 있으면 길이 남지 않는다 ──────────────────────

def test_signin_is_refused_and_says_what_to_do_instead(off):
    _says_use_the_company_account(_auth_client().post("/api/auth/signin", json=_CREDS))


def test_signup_is_refused_and_says_what_to_do_instead(off):
    _says_use_the_company_account(_auth_client().post("/api/auth/signup", json=_CREDS))


def test_admin_request_reset_is_refused(off):
    """쓸 수 없는 비밀번호를 되찾아 주는 접수창은 덫이다 — 사람은 기다린다."""
    _says_use_the_company_account(
        _auth_client().post("/api/auth/password-reset-request",
                            json={"department": "D", "name": "홍길동"}))


def test_google_self_reset_start_is_refused(off):
    _says_use_the_company_account(
        _auth_client().get("/api/auth/password-reset/google/start",
                           follow_redirects=False))


def test_google_self_reset_land_is_refused(off):
    _says_use_the_company_account(
        _auth_client().get("/api/auth/password-reset/google/land?rc=x",
                           follow_redirects=False))


def test_google_self_reset_complete_is_refused(off):
    _says_use_the_company_account(
        _auth_client().post("/api/auth/password-reset/google/complete",
                            json={"new_password": "secret123"}))


def test_admin_temporary_password_is_refused(off):
    """⛔ 여기가 가장 위험하다 — 발급하면 못 쓰는 열쇠를 주면서 문까지 잠근다.

    `must_change_password` 가 서면 그 사람은 **회사 계정으로 들어와도** 모든
    요청이 막히는데, 그것을 푸는 유일한 길이 비밀번호 로그인이다.
    """
    _says_use_the_company_account(
        _admin_client().post("/api/admin/ad/users/5/reset-password"))


def test_google_callback_reset_branch_is_refused_with_readable_html(off, monkeypatch):
    """구글 화면을 띄워 둔 채 오래 있다 돌아오는 사람이 있다.

    ⚠️ 여기서는 JSON 이 아니라 **HTML 안내**여야 한다 — 브라우저 최상위 이동이라
       날것의 `{"detail": ...}` 을 사용자가 그대로 보게 된다.
    """
    monkeypatch.setattr(auth_routes.password_reset_google, "is_reset_state",
                        lambda _state: True)

    response = _reset_callback_client().get(
        "/auth/google/callback?code=c&state=s", follow_redirects=False)

    _says_use_the_company_account(response)
    assert "<html" in response.text.lower(), "브라우저에 날것의 JSON 이 나갔다"


# ────────────────────── ② 켜면 예전 그대로 돌아온다 ──────────────────────
#
# ⛔ 여기가 이 파일의 핵심이다. 아래가 깨지면 "껐다" 가 아니라 "지웠다" 이고,
#    장애 중에 되돌릴 수단이 없다는 뜻이다.

def test_signup_runs_its_own_validation_again_when_switched_on(on):
    """관문이 안 선다 — 원래의 길이 검사(400)가 답한다."""
    response = _auth_client().post(
        "/api/auth/signup", json={"department": "D", "name": "홍길동", "password": "1"})

    assert response.status_code == 400
    assert "4자 이상" in response.text


def test_signin_reaches_its_own_lookup_again_when_switched_on(on, monkeypatch):
    """관문이 안 선다 — AD 조회까지 가서 원래의 401 이 나온다."""
    async def _no_such_user(*_args, **_kwargs):
        return None

    monkeypatch.setattr(auth_api, "_lookup_ad_user", _no_such_user)
    monkeypatch.setattr(auth_api, "_not_found_detail",
                        _async_value("사용자를 찾을 수 없습니다"))

    response = _auth_client().post("/api/auth/signin", json=_CREDS)

    assert response.status_code == 401
    assert "회사 계정" not in response.text


def test_request_reset_is_accepted_again_when_switched_on(on, monkeypatch):
    async def _no_such_user(*_args, **_kwargs):
        return None

    monkeypatch.setattr(auth_api, "_lookup_ad_user", _no_such_user)

    response = _auth_client().post("/api/auth/password-reset-request",
                                   json={"department": "D", "name": "홍길동"})

    assert response.status_code == 200
    assert response.json()["ok"] is True


def test_google_self_reset_complete_reaches_its_own_checks_when_switched_on(on):
    """길이 검사가 다시 먼저 선다 (400) — 403 이 아니다."""
    response = _auth_client().post("/api/auth/password-reset/google/complete",
                                   json={"new_password": "1"})

    assert response.status_code == 400
    assert "회사 계정" not in response.text


def test_google_self_reset_land_redirects_again_when_switched_on(on, monkeypatch):
    from app.core import password_reset_google

    monkeypatch.setattr(password_reset_google, "peek_grant", lambda _c: None)

    response = _auth_client().get("/api/auth/password-reset/google/land?rc=x",
                                  follow_redirects=False)

    assert response.status_code == 303
    assert "/login?reset=expired" in response.headers["location"]


def test_admin_temporary_password_reaches_its_own_lookup_when_switched_on(on, monkeypatch):
    async def _no_target(*_args, **_kwargs):
        return None

    monkeypatch.setattr(admin_group_api, "_fetch_one", _no_target)

    response = _admin_client().post("/api/admin/ad/users/5/reset-password")

    assert response.status_code == 404
    assert "회사 계정" not in response.text


def test_google_callback_reset_branch_runs_again_when_switched_on(on, monkeypatch):
    monkeypatch.setattr(auth_routes.password_reset_google, "is_reset_state",
                        lambda _state: True)

    def _reject(_state):
        raise ValueError("expired")

    monkeypatch.setattr(auth_routes.password_reset_google, "consume_state", _reject)

    response = _reset_callback_client().get(
        "/auth/google/callback?code=c&state=s", follow_redirects=False)

    # 관문이 아니라 **원래의** state 검사가 답했다.
    assert response.status_code == 400
    assert "확인 링크가 만료" in response.text


# ────────────────────── 관문이 맨 앞에 서 있는가 ──────────────────────

@pytest.mark.parametrize("fn", [
    auth_api.signup,
    auth_api.signin,
    auth_api.password_reset_request,
    auth_api.password_reset_google_start,
    auth_api.password_reset_google_land,
    auth_api.password_reset_google_complete,
    admin_group_api.reset_password,
])
def test_the_gate_stands_before_any_db_work_or_hashing(fn):
    """⚠️ 관문이 뒤에 있으면 꺼진 경로가 계속 조회·해싱을 태우고,
       응답 시간으로 계정 존재 여부가 샌다. 일회용 증표도 먼저 소모된다."""
    body = inspect.getsource(fn)
    gate = body.index("require_password_login()")
    for later in ("bcrypt", "_fetch_one", "_db_fetch_one", "_lookup_ad_user",
                  "consume_grant", "peek_grant", "issue_state"):
        at = body.find(later, 0, gate)
        assert at == -1, f"{fn.__name__}: 관문보다 앞에서 {later} 를 한다"


# ────────────────────── 일부러 열어 둔 것 ──────────────────────

def test_change_password_is_deliberately_left_open():
    """⛔ `/api/auth/change-password` 는 **막지 않았다.** 잠금 사고를 막는다.

    `must_change_password` 가 서 있는 사람은 회사 계정으로 들어와도 모든 요청이
    `get_current_user` 관문에서 막히는데, 그 깃발을 내리는 곳이 여기뿐이다.
    여기까지 닫으면 그 사람은 **어느 문으로도 못 들어온다.**

    로그인 수단이 아니라는 점도 근거다 — 이미 세션이 있고 현재 비밀번호를
    아는 사람만 부를 수 있다. 나중에 닫기로 한다면 그것은 **의식적인 결정**이어야
    하고, 그때는 이 테스트를 지우면서 잠금 경로를 함께 처리해야 한다.
    """
    assert "require_password_login()" not in inspect.getsource(auth_api.change_password)


# ────────────────────── 화면은 서버에 묻는다 ──────────────────────

def test_methods_endpoint_reports_both_switches(monkeypatch):
    from app.api import entra_routes

    monkeypatch.setattr(auth_api, "get_settings", lambda: _Cfg(False))
    monkeypatch.setattr(entra_routes, "is_available", lambda _request: True)

    body = _auth_client().get("/api/auth/methods").json()

    assert body == {"password": False, "entra": True}


def test_methods_flips_with_the_setting(monkeypatch):
    from app.api import entra_routes

    monkeypatch.setattr(auth_api, "get_settings", lambda: _Cfg(True))
    monkeypatch.setattr(entra_routes, "is_available", lambda _request: False)

    assert _auth_client().get("/api/auth/methods").json() == {
        "password": True, "entra": False}


def test_entra_judgement_is_not_written_twice():
    """⛔ `/auth/entra/status` 와 `/api/auth/methods` 가 같은 판정을 각자 적으면
       언젠가 갈리고, 갈리면 화면이 거짓말을 한다 (이 프로젝트의 단골 사고)."""
    from app.api import entra_routes

    src = inspect.getsource(auth_api.auth_methods)
    assert "is_available" in src
    assert "is_configured" not in src, "판정을 여기서 다시 적었다"
    assert "redirect_uri_is_usable" not in src, "판정을 여기서 다시 적었다"

    status_src = inspect.getsource(entra_routes.entra_status)
    assert "is_available" in status_src, "status 가 공통 판정을 안 쓴다"


def test_login_page_hides_the_password_form_only_on_an_explicit_false():
    """⛔ 물어보지 못했으면 **숨기지 않는다.**

    여기서 닫는 쪽으로 실패하면 아무도 로그인할 수 없다. 열어 두면 최악이라야
    "눌렀더니 안내 문구가 뜨는" 되돌릴 수 있는 실패다. 회사 계정 버튼과는
    기울어야 할 방향이 정반대라 코드에 함께 적혀 있어야 한다.
    """
    src = (_ROOT / "app" / "frontend" / "auth.js").read_text(encoding="utf-8")

    assert "/api/auth/methods" in src, "화면이 서버에 묻지 않는다"
    # ⛔ `!data.password` 는 필드가 없을 때도 참이 된다 — 조용히 폼이 사라진다.
    assert "data.password !== false" in src
    assert "!data.password" not in src

    gate = src.index("data.password !== false")
    body = src[gate:]
    hide = body.index("form.hidden = true")
    assert "catch" not in src[gate:gate + hide], "fetch 실패 경로에서 폼을 숨긴다"

    # 회사 계정 버튼이 실제로 떠 있을 때만 닫는다 — 둘 다 꺼지면 빈 카드가 된다.
    assert "data.entra !== true" in src


def test_login_page_hides_every_password_control_together():
    """폼만 숨기고 링크를 남기면 눌러도 아무 일 없는 자리가 남는다."""
    src = (_ROOT / "app" / "frontend" / "auth.js").read_text(encoding="utf-8")
    tail = src[src.index("data.password !== false"):]

    for control in ("form.hidden = true", "toggleLink.hidden = true",
                    "forgotLink.hidden = true", "forgotBox.hidden = true",
                    "divider.hidden = true"):
        assert control in tail, f"숨기지 않는 것이 있다: {control}"


def test_one_question_not_two():
    """⛔ 두 번 물어보면 응답이 엇갈렸을 때 두 수단이 동시에 숨거나 동시에 뜬다."""
    src = (_ROOT / "app" / "frontend" / "auth.js").read_text(encoding="utf-8")
    assert src.count('fetch("/auth/entra/status")') == 0
    assert src.count('fetch("/api/auth/methods")') == 1


def _async_value(value):
    async def _fn(*_args, **_kwargs):
        return value
    return _fn


def test_계정을_못_이었을_때_할_수_없는_일을_시키지_않는다():
    """⛔ ID/PW 로그인을 끈 뒤 "기존 방식으로 로그인하세요" 는 거짓 안내다.

    회사 계정은 멀쩡한데 셀라 계정에 못 이어진 사람에게, 이제 존재하지 않는
    경로를 시키면 그 사람은 아무것도 할 수 없다. 오늘 내내 고친 「낡으면
    거짓이 되는 문장」과 같은 부류다.
    """
    src = open("app/api/entra_routes.py", encoding="utf-8").read()
    body = src[src.index("entra_no_matching_account"):]
    body = body[:body.index("status=404")]
    assert "기존 방식으로" not in body, "없어진 로그인 방식을 안내하고 있다"
    assert "관리자에게 문의" in body
