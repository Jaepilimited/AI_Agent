"""End-to-end (browser-level) check of the forced password-change screen.

Backend enforcement (the 403 gate in app/api/auth_middleware.py) is covered by
tests/test_password_reset.py. This file drives the REAL chat.html + chat.js in a
real browser against a mocked backend to verify the person on the other end of a
reset actually sees something that explains itself, cannot be dismissed into a
half-broken app, and lands in a working app afterwards without a re-login —
mirroring reset -> log in -> forced screen -> change -> app works.

We cannot exercise this against a live server + real MariaDB account from this
environment, so every network call is intercepted and replayed with fixture data;
everything else (markup, chat.js logic, CSS) is the real shipped code.
"""

import json
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
CHAT_HTML = (ROOT / "app/frontend/chat.html").read_text(encoding="utf-8")

# Real local files chat.html pulls in — served as themselves so the actual shipped
# behavior runs, not a stand-in.
_LOCAL_FILES = {
    "/frontend/chat.js": ("app/frontend/chat.js", "application/javascript"),
    "/frontend/personal-briefing.js": ("app/frontend/personal-briefing.js", "application/javascript"),
    "/frontend/answer-loading.js": ("app/frontend/answer-loading.js", "application/javascript"),
    "/frontend/cella-pet.js": ("app/frontend/cella-pet.js", "application/javascript"),
    "/static/style.css": ("app/static/style.css", "text/css"),
    "/static/cella-pet.css": ("app/static/cella-pet.css", "text/css"),
    "/static/purify.min.js": ("app/static/purify.min.js", "application/javascript"),
    "/static/chart.umd.min.js": ("app/static/chart.umd.min.js", "application/javascript"),
}
_LOCAL_FILE_CACHE = {
    path: (ROOT / rel).read_text(encoding="utf-8") for path, (rel, _ct) in _LOCAL_FILES.items()
}

_ME_REGISTERED = {
    "id": 42,
    "email": "kim@example.com",
    "name": "Kim",
    "department": "OP",
    "role": "user",
    "can_view_fi": False,
    "must_change_password": True,
    "allowed_models": ["skin1004-Analysis"],
    "brand_filters": [],
    "my_brand_filter": None,
}


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as playwright:
        instance = playwright.chromium.launch()
        yield instance
        instance.close()


def _url_path(url: str) -> str:
    # strip scheme+host, keep path only (ignore query string)
    after_scheme = url.split("://", 1)[-1]
    path = "/" + after_scheme.split("/", 1)[1] if "/" in after_scheme else "/"
    return path.split("?", 1)[0]


def _install_backend(page, me_sequence, calls):
    """Route every request. `me_sequence` is popped from (left to right) on each
    GET /api/auth/me — the last value repeats once exhausted. Everything not
    explicitly modeled gets an inert 200 so unrelated app wiring (conversation
    list, system status, announcements, ...) never hangs or navigates the page
    away; it's irrelevant to what this test is checking.
    """

    def handler(route):
        req = route.request
        path = _url_path(req.url)

        if path in ("/chat", "/"):
            route.fulfill(status=200, content_type="text/html", body=CHAT_HTML)
            return

        if path == "/api/auth/me" and req.method == "GET":
            calls.setdefault("me", 0)
            calls["me"] += 1
            idx = min(calls["me"], len(me_sequence)) - 1
            route.fulfill(status=200, content_type="application/json", body=json.dumps(me_sequence[idx]))
            return

        if path == "/api/auth/change-password" and req.method == "POST":
            calls.setdefault("change_password_bodies", [])
            calls["change_password_bodies"].append(json.loads(req.post_data or "{}"))
            route.fulfill(status=200, content_type="application/json", body=json.dumps({"ok": True}))
            return

        if path == "/api/conversations" and req.method == "GET":
            # Only fetched from inside _finishInit() — seeing it proves normal
            # app init actually ran, not just that the overlay was removed.
            calls["conversations_fetched"] = calls.get("conversations_fetched", 0) + 1
            route.fulfill(status=200, content_type="application/json", body="[]")
            return

        if path in _LOCAL_FILE_CACHE:
            _rel, ctype = _LOCAL_FILES[path]
            route.fulfill(status=200, content_type=ctype, body=_LOCAL_FILE_CACHE[path])
            return

        # Everything else (external CDN scripts, other /api/* polling, favicon, ...)
        rtype = req.resource_type
        if rtype == "stylesheet":
            route.fulfill(status=200, content_type="text/css", body="")
        elif rtype == "script":
            route.fulfill(status=200, content_type="application/javascript", body="")
        elif rtype in ("xhr", "fetch"):
            route.fulfill(status=200, content_type="application/json", body="{}")
        else:
            route.fulfill(status=200, content_type="text/plain", body="")

    page.route("**/*", handler)


def test_forced_screen_explains_itself_and_cannot_be_dismissed(browser):
    """The person who reset their password sees WHY, not a broken app."""
    context = browser.new_context()
    page = context.new_page()
    calls = {}
    _install_backend(page, [_ME_REGISTERED], calls)

    page.goto("http://test.local/chat")
    page.wait_for_selector(".pw-modal")

    modal_text = page.locator(".pw-modal").inner_text()
    assert "관리자가 비밀번호를 초기화했습니다" in modal_text
    assert "임시 비밀번호" in modal_text

    # not dismissable: no cancel button, backdrop click does nothing
    assert page.locator("#pw-cancel").count() == 0
    page.locator(".admin-modal-overlay").click(position={"x": 5, "y": 5})
    assert page.locator(".pw-modal").count() == 1

    # and the rest of the app genuinely didn't start up behind the modal —
    # this isn't just cosmetic, real init() was short-circuited
    assert calls.get("conversations_fetched", 0) == 0

    context.close()


def test_temp_password_goes_in_the_current_password_field(browser):
    """Submitting fills current_password with the temp password, not a chosen one."""
    context = browser.new_context()
    page = context.new_page()
    calls = {}
    _install_backend(page, [_ME_REGISTERED, dict(_ME_REGISTERED, must_change_password=False)], calls)

    page.goto("http://test.local/chat")
    page.wait_for_selector(".pw-modal")

    page.fill("#pw-current", "Kx7mQ2pR9wLz")
    page.fill("#pw-new", "brand-new-pw")
    page.fill("#pw-confirm", "brand-new-pw")
    with page.expect_request(lambda r: _url_path(r.url) == "/api/auth/change-password"):
        page.click("#pw-submit")

    assert calls.get("change_password_bodies") == [
        {"current_password": "Kx7mQ2pR9wLz", "new_password": "brand-new-pw"}
    ]

    context.close()


def test_success_reaches_a_working_app_without_relogin(browser):
    """After changing the password: no navigation to /login, no leftover forced
    modal, and normal init (which only runs post-gate) actually ran."""
    context = browser.new_context()
    page = context.new_page()
    calls = {}
    _install_backend(page, [_ME_REGISTERED, dict(_ME_REGISTERED, must_change_password=False)], calls)

    navigated = []
    page.on("framenavigated", lambda frame: navigated.append(frame.url))

    page.goto("http://test.local/chat")
    page.wait_for_selector(".pw-modal")

    page.fill("#pw-current", "Kx7mQ2pR9wLz")
    page.fill("#pw-new", "brand-new-pw")
    page.fill("#pw-confirm", "brand-new-pw")
    page.click("#pw-submit")

    page.wait_for_selector("#pw-done")
    # _completeInitAfterForcedPasswordChange -> _finishInit() is async and not
    # awaited by the click handler — wait for its telltale request instead of
    # racing it with a fixed sleep.
    with page.expect_request(lambda r: _url_path(r.url) == "/api/conversations"):
        page.click("#pw-done")

    # the modal is gone and stayed gone — no re-appearance, no bounce to /login
    page.wait_for_selector(".admin-modal-overlay", state="detached")
    assert calls["me"] == 2, "must re-check /me after the change instead of assuming it worked"
    assert not any(u.endswith("/login") for u in navigated), "must not force a re-login when the session is still valid"

    # proof normal init actually continued: loadConversations() is only ever
    # called from inside _finishInit(), which the forced branch skips entirely
    # until the password is changed.
    assert calls.get("conversations_fetched", 0) >= 1
    assert page.locator("#user-name").inner_text() == "Kim"
    assert page.locator("#admin-btn-wrap").is_hidden()

    context.close()


def test_user_footer_gear_owns_briefing_and_password_settings(browser):
    """One visible gear replaces the lock and reaches both briefing and password settings."""
    context = browser.new_context()
    page = context.new_page()
    page.set_default_timeout(3000)
    calls = {}
    normal_user = dict(_ME_REGISTERED, must_change_password=False)
    _install_backend(page, [normal_user], calls)

    page.goto("http://test.local/chat")
    page.wait_for_selector("#btn-briefing-settings")

    settings = page.locator("#btn-briefing-settings")
    assert settings.is_visible()
    assert page.locator("#btn-change-pw").count() == 0
    assert page.evaluate(
        """() => {
          const settings = document.querySelector('#btn-briefing-settings');
          return settings
            && settings.previousElementSibling.id === 'user-name'
            && settings.nextElementSibling.id === 'btn-logout';
        }"""
    ) is True

    settings.click()
    dialog = page.get_by_role("dialog", name="설정", exact=True)
    assert dialog.is_visible()
    assert dialog.locator(".briefing-settings-section-title").all_inner_texts() == [
        "계정",
        "내가 저장한 보고",
        "잔디로 받기",
        "노션으로 받기",
    ]
    dialog.get_by_role("button", name="비밀번호 변경").click()
    assert dialog.count() == 0
    assert page.locator(".pw-modal").is_visible()

    context.close()
