# -*- coding: utf-8 -*-
"""비밀번호 재설정 **요청 접수** 회귀.

**왜 요청만 받나**: 셀프 재설정을 하려면 본인 확인 수단이 있어야 하는데,
메일(SMTP 25/587 차단)·AD(WAS 에서 LDAP 미도달)가 전부 막혀 있다.

⛔ **부서·이름만으로 재설정되게 하면 안 된다.** 그 둘은 로그인 화면이 이미 목록으로
   보여 준다 — 누구나 남의 계정을 초기화할 수 있게 되고, 셀라에는 재무 손익(FI)
   데이터가 있다. 그래서 요청은 계정에 **아무 변화도 주지 않는다.**
"""
from __future__ import annotations

import inspect

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core import password_reset as PR


def test_the_request_never_touches_the_account():
    """⛔ 이 모듈이 비밀번호나 해시를 건드리면 설계가 무너진다."""
    src = inspect.getsource(PR)
    for forbidden in ("password_hash", "bcrypt", "hashpw"):
        assert forbidden not in src, f"요청 접수가 계정을 건드린다: {forbidden}"


def test_the_endpoint_answers_the_same_whether_or_not_the_person_exists():
    """⚠️ 존재 여부를 알려 주면 이 엔드포인트가 재직자 조회기가 된다.

    ⛔ 그렇다고 조용히 넘기면 안 된다 — 이름이 안 맞아 못 찾는 경우가 실제로 있고,
       그때 사용자는 접수됐다고 믿고 기다린다. 로그에는 반드시 남긴다.
    """
    from app.api import auth_api

    src = inspect.getsource(auth_api.password_reset_request)
    assert src.count("return same") == 2, "찾은 경우와 못 찾은 경우의 응답이 갈렸다"
    assert "password_reset_request_no_match" in src, "못 찾은 것을 로그에 안 남긴다"
    # ⛔ 프로덕션은 앱 INFO 를 통째로 버린다 (CLAUDE.md)
    assert "logger.warning" in src and "logger.info" not in src


def _public_auth_client() -> TestClient:
    from app.api import auth_api

    app = FastAPI()
    app.include_router(auth_api.auth_api_router)
    return TestClient(app, raise_server_exceptions=False)


def test_request_keeps_personal_details_out_of_logs(monkeypatch, password_login_on):
    """공개 엔드포인트의 이름·부서·메모는 운영 로그에 남기지 않는다."""
    from app.api import auth_api

    events = []

    class FakeLogger:
        def warning(self, event, **fields):
            events.append((event, fields))

    async def found_user(*_args):
        return {"id": 42, "display_name": "홍길동", "department": "데이터팀"}

    monkeypatch.setattr(auth_api, "logger", FakeLogger())
    monkeypatch.setattr(auth_api, "_lookup_ad_user", found_user)
    monkeypatch.setattr(PR, "create", lambda *_args: {"ok": True, "duplicate": False})

    body = {"id": 42, "name": "홍길동", "department": "데이터팀", "note": "개인 메모"}
    response = _public_auth_client().post("/api/auth/password-reset-request", json=body)

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "message": "요청이 접수되었습니다. 관리자가 확인 후 연락드립니다.",
    }
    logged = repr(events)
    for personal_value in ("홍길동", "데이터팀", "개인 메모"):
        assert personal_value not in logged


def test_unknown_person_gets_the_same_response_without_leaking_their_input(
        monkeypatch, password_login_on):
    from app.api import auth_api

    events = []

    class FakeLogger:
        def warning(self, event, **fields):
            events.append((event, fields))

    async def no_user(*_args):
        return None

    monkeypatch.setattr(auth_api, "logger", FakeLogger())
    monkeypatch.setattr(auth_api, "_lookup_ad_user", no_user)

    body = {"name": "없는사람", "department": "없는팀", "note": "비공개 메모"}
    response = _public_auth_client().post("/api/auth/password-reset-request", json=body)

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "message": "요청이 접수되었습니다. 관리자가 확인 후 연락드립니다.",
    }
    logged = repr(events)
    for personal_value in ("없는사람", "없는팀", "비공개 메모"):
        assert personal_value not in logged


def test_reset_and_closing_the_request_are_one_action():
    """⛔ 따로 두면 관리자가 초기화만 하고 요청은 대기로 남아 다음에도 맨 앞에 뜬다 —
    붐따가 정확히 그렇게 36건 쌓였다."""
    from app.api import admin_group_api

    src = inspect.getsource(admin_group_api.reset_password)
    assert "close_for_ad_user" in src


def test_the_admin_list_says_whether_the_person_can_be_reset_at_all():
    """⚠️ 가입 전이면 초기화할 대상이 없다 — 초기화가 아니라 가입을 안내해야 한다."""
    from app.api import admin_group_api

    src = inspect.getsource(admin_group_api.list_password_reset_requests)
    assert '"registered"' in src


@pytest.mark.parametrize("field", ["MAX_OPEN_PER_USER", "MAX_NOTE"])
def test_abuse_limits_exist(field):
    """로그인 없이 열린 경로다 — 도배와 긴 입력을 코드가 막아야 한다."""
    assert getattr(PR, field) > 0


def test_open_requests_query_joins_registration_state():
    """대기 목록은 가입 여부를 **조회로** 판정한다 (화면이 추측하지 않는다)."""
    src = inspect.getsource(PR.open_requests)
    assert "LEFT JOIN users" in src and "registered" in src


def test_the_login_screen_offers_an_admin_reset_request():
    """로그인하지 못해도 요청을 남길 수 있어야 한다."""
    import io
    import os

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    html = io.open(os.path.join(root, "app", "frontend", "login.html"),
                   encoding="utf-8").read()
    assert 'id="forgot-link"' in html and 'id="forgot-box"' in html, "요청 경로가 없다"
    assert "관리자에게 요청" in html
    assert "비밀번호를 잊으셨나요" in html


def test_reset_action_is_defined_once_in_the_frontend():
    """⛔ 목록과 요청 배너가 각자 초기화 코드를 갖고 있으면 한쪽만 고쳐진다
    (direct 프롬프트가 두 벌이던 사고와 같은 계열)."""
    import io
    import os

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    js = io.open(os.path.join(root, "app", "frontend", "chat.js"), encoding="utf-8").read()
    assert js.count("function resetPasswordFor(") == 1
    assert js.count("/reset-password\", { method: \"POST\" })") == 1, "초기화 호출이 두 벌이다"
