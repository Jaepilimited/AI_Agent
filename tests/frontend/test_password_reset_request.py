"""Browser contract for the login-screen password-reset request flow."""

import json
from pathlib import Path

import pytest
from playwright.sync_api import expect, sync_playwright


ROOT = Path(__file__).resolve().parents[2]
LOGIN_HTML = (ROOT / "app/frontend/login.html").read_text(encoding="utf-8")
_LOCAL_FILES = {
    "/frontend/auth.js": ("app/frontend/auth.js", "application/javascript"),
    "/static/style.css": ("app/static/style.css", "text/css"),
}
_LOCAL_FILE_CACHE = {
    path: (ROOT / rel).read_text(encoding="utf-8")
    for path, (rel, _content_type) in _LOCAL_FILES.items()
}


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as playwright:
        instance = playwright.chromium.launch()
        yield instance
        instance.close()


def _url_path(url: str) -> str:
    after_scheme = url.split("://", 1)[-1]
    path = "/" + after_scheme.split("/", 1)[1] if "/" in after_scheme else "/"
    return path.split("?", 1)[0]


def _install_backend(page, calls):
    def handler(route):
        request = route.request
        path = _url_path(request.url)
        if path == "/login":
            route.fulfill(status=200, content_type="text/html", body=LOGIN_HTML)
            return
        if path == "/api/auth/me":
            # The login page redirects only for an already authenticated session.
            route.fulfill(status=401, content_type="application/json", body='{"detail":"unauthorized"}')
            return
        if path == "/api/auth/search-name":
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps([{
                    "id": 42,
                    "display_name": "홍길동",
                    "department": "운영본부 > 데이터팀",
                    "email": "hong@example.com",
                }]),
            )
            return
        if path == "/api/auth/password-reset-request" and request.method == "POST":
            calls["request"] = json.loads(request.post_data or "{}")
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps({
                    "ok": True,
                    "message": "요청이 접수되었습니다. 관리자가 확인 후 연락드립니다.",
                }),
            )
            return
        if path in _LOCAL_FILE_CACHE:
            _rel, content_type = _LOCAL_FILES[path]
            route.fulfill(status=200, content_type=content_type, body=_LOCAL_FILE_CACHE[path])
            return
        if request.resource_type == "stylesheet":
            route.fulfill(status=200, content_type="text/css", body="")
        elif request.resource_type == "script":
            route.fulfill(status=200, content_type="application/javascript", body="")
        else:
            route.fulfill(status=200, content_type="text/plain", body="")

    page.route("**/*", handler)


def test_login_user_can_submit_a_password_reset_request(browser):
    """사용자는 기존 비밀번호나 회사 계정 비밀번호를 입력하지 않는다."""
    context = browser.new_context()
    page = context.new_page()
    calls = {}
    _install_backend(page, calls)

    page.goto("http://test.local/login")
    page.fill("#input-name", "홍길동")
    page.locator(".ac-item").wait_for(state="visible")
    page.locator(".ac-item").click()

    page.locator("#forgot-link").click()
    page.locator("#forgot-box").wait_for(state="visible")
    page.fill("#forgot-note", "비밀번호를 찾을 수 없습니다")
    with page.expect_request(
        lambda request: _url_path(request.url) == "/api/auth/password-reset-request"
    ):
        page.locator("#forgot-submit").click()

    assert calls["request"] == {
        "department": "운영본부 > 데이터팀",
        "name": "홍길동",
        "id": 42,
        "note": "비밀번호를 찾을 수 없습니다",
    }
    expect(page.locator("#forgot-msg")).to_contain_text("관리자가 확인 후 연락드립니다")
    assert page.locator("#input-password").is_visible()
    context.close()
