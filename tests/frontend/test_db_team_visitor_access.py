"""Browser contract for the DB team's narrow Admin/Visitor permission."""

import json
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[2]
CHAT_HTML = (ROOT / "app/frontend/chat.html").read_text(encoding="utf-8")
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
    path: (ROOT / rel).read_text(encoding="utf-8") for path, (rel, _ctype) in _LOCAL_FILES.items()
}

_BASE_ME = {
    "id": 52,
    "email": "db@example.com",
    "name": "DB User",
    "department": "Craver_Accounts > Users > 브랜드부문 > 운영본부 > 데이터 비즈니스팀 > 데이터분석파트",
    "role": "user",
    "can_view_fi": False,
    "can_view_visitor_analytics": True,
    "must_change_password": False,
    "allowed_models": ["skin1004-Analysis"],
    "brand_filters": [],
    "my_brand_filter": None,
}

_VISITOR_RESPONSE = {
    "summary": {"unique_visitors": 3, "today_visitors": 1, "page_visits": 7, "registered_users": 20},
    "series": [],
    "visitors": [],
    "tracking_started_at": "2026-08-11",
    "range": {"days": 30, "granularity": "day", "is_partial": True},
    "availability": {"tracked_days": 21, "available_ranges": [30], "comparison_ready": False},
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


def _install_backend(page, me, calls):
    def handler(route):
        request = route.request
        path = _url_path(request.url)
        if path in ("/chat", "/"):
            route.fulfill(status=200, content_type="text/html", body=CHAT_HTML)
            return
        if path == "/api/auth/me":
            route.fulfill(status=200, content_type="application/json", body=json.dumps(me))
            return
        if path == "/api/conversations":
            route.fulfill(status=200, content_type="application/json", body="[]")
            return
        if path == "/api/admin/visitor-analytics":
            calls["visitors"] = calls.get("visitors", 0) + 1
            calls.setdefault("visitor_urls", []).append(request.url)
            route.fulfill(status=200, content_type="application/json", body=json.dumps(_VISITOR_RESPONSE))
            return
        if path == "/api/admin/directory/users":
            calls["directory_users"] = calls.get("directory_users", 0) + 1
            route.fulfill(status=200, content_type="application/json", body=json.dumps([{
                "id": 17,
                "username": "finance.user",
                "display_name": "Finance User",
                "email": "finance@example.com",
                "department": "운영본부 > 재무팀",
                "can_view_fi": False,
                "can_view_visitor_analytics": False,
                "user_id": 74,
                "group_names": None,
                "entra_linked": True,
                "identity_source": "entra",
                "last_signin_at": None,
            }]))
            return
        if path == "/api/admin/directory/users/17/visitor-analytics" and request.method == "PUT":
            calls["visitor_access_body"] = json.loads(request.post_data or "{}")
            route.fulfill(status=200, content_type="application/json", body='{"ok":true}')
            return
        if path.startswith("/api/admin/"):
            calls.setdefault("other_admin", []).append(path)
            route.fulfill(status=403, content_type="application/json", body='{"detail":"forbidden"}')
            return
        if path in _LOCAL_FILE_CACHE:
            _rel, ctype = _LOCAL_FILES[path]
            route.fulfill(status=200, content_type=ctype, body=_LOCAL_FILE_CACHE[path])
            return
        if request.resource_type == "stylesheet":
            route.fulfill(status=200, content_type="text/css", body="")
        elif request.resource_type == "script":
            route.fulfill(status=200, content_type="application/javascript", body="")
        elif request.resource_type in ("xhr", "fetch"):
            route.fulfill(status=200, content_type="application/json", body="{}")
        else:
            route.fulfill(status=200, content_type="text/plain", body="")

    page.route("**/*", handler)


def test_db_team_sees_only_the_visitor_tab_and_loads_it(browser):
    context = browser.new_context()
    page = context.new_page()
    calls = {}
    _install_backend(page, _BASE_ME, calls)

    page.goto("http://test.local/chat")
    page.wait_for_selector("#admin-btn-wrap", state="visible")
    page.click("#btn-admin")
    page.wait_for_selector("#tab-visitors.active")

    assert page.locator(".admin-tab:visible").count() == 1
    assert page.locator(".admin-tab:visible").get_attribute("data-tab") == "visitors"
    assert page.locator("#admin-stats-bar").is_hidden()
    assert calls.get("visitors", 0) == 1
    assert calls.get("other_admin", []) == []
    context.close()


def test_ordinary_user_still_cannot_see_admin_entry(browser):
    context = browser.new_context()
    page = context.new_page()
    calls = {}
    _install_backend(page, dict(_BASE_ME, department="운영본부 > 재무팀", can_view_visitor_analytics=False), calls)

    page.goto("http://test.local/chat")
    page.wait_for_selector("#user-name", state="visible")
    assert page.locator("#admin-btn-wrap").is_hidden()
    assert calls.get("visitors", 0) == 0
    context.close()


def test_admin_still_sees_every_admin_tab(browser):
    context = browser.new_context()
    page = context.new_page()
    calls = {}
    _install_backend(page, dict(_BASE_ME, role="admin", department="", can_view_visitor_analytics=True), calls)

    page.goto("http://test.local/chat")
    page.wait_for_selector("#admin-btn-wrap", state="visible")
    page.click("#btn-admin")
    page.wait_for_selector("#skin-admin-drawer.open")
    assert page.locator(".admin-tab:visible").count() == page.locator(".admin-tab").count()
    assert page.locator("#admin-stats-bar").is_visible()
    context.close()


def test_admin_can_grant_visitor_analytics_from_the_directory_user_row(browser):
    """Removing the real Admin checkbox or sending the wrong update must fail this UI contract."""
    context = browser.new_context()
    page = context.new_page()
    page.set_default_timeout(2500)
    calls = {}
    _install_backend(page, dict(_BASE_ME, role="admin", department="", can_view_visitor_analytics=True), calls)

    page.goto("http://test.local/chat")
    page.click("#btn-admin")
    page.click('.admin-tab[data-tab="users"]')
    checkbox = page.locator('.admin-visitor-toggle[data-ad-user-id="17"]')
    checkbox.wait_for(state="visible")
    with page.expect_request(lambda request: _url_path(request.url) == "/api/admin/directory/users/17/visitor-analytics"):
        checkbox.check()

    assert calls["visitor_access_body"] == {"can_view_visitor_analytics": True}
    context.close()


def test_admin_can_reach_visitor_permission_management_from_visitors_tab(browser):
    """The visitor analytics view must provide a discoverable route to grant access."""
    context = browser.new_context()
    page = context.new_page()
    page.set_default_timeout(2500)
    calls = {}
    _install_backend(page, dict(_BASE_ME, role="admin", department="", can_view_visitor_analytics=True), calls)

    page.goto("http://test.local/chat")
    page.click("#btn-admin")
    page.click('.admin-tab[data-tab="visitors"]')
    page.locator("#btn-manage-visitor-access").click()

    assert page.locator("#tab-users").get_attribute("class") == "admin-tab-content active"
    assert page.locator("#admin-search").is_visible()
    context.close()


def test_visitor_sort_controls_request_server_rankings(browser):
    """Changing the visible ranking must ask the API for that full top-50 set."""
    context = browser.new_context()
    page = context.new_page()
    page.set_default_timeout(2500)
    calls = {}
    _install_backend(page, dict(_BASE_ME, role="admin", department=""), calls)

    page.goto("http://test.local/chat")
    page.click("#btn-admin")
    with page.expect_request(
        lambda request: _url_path(request.url) == "/api/admin/visitor-analytics"
        and "sort=recent" in request.url
    ):
        page.click('.admin-tab[data-tab="visitors"]')

    recent = page.locator('.visitor-sort-btn[data-sort="recent"]')
    active_days = page.locator('.visitor-sort-btn[data-sort="active_days"]')
    visits = page.locator('.visitor-sort-btn[data-sort="visits"]')
    assert recent.get_attribute("aria-pressed") == "true"

    with page.expect_request(lambda request: "sort=active_days" in request.url):
        active_days.click()
    assert active_days.get_attribute("aria-pressed") == "true"
    assert recent.get_attribute("aria-pressed") == "false"

    with page.expect_request(lambda request: "sort=visits" in request.url):
        visits.click()
    assert visits.get_attribute("aria-pressed") == "true"
    assert calls["visitors"] == 3
    context.close()


def test_visitor_only_user_is_told_that_an_admin_manages_access(browser):
    """A visitor viewer must not mistake read access for permission-management access."""
    context = browser.new_context()
    page = context.new_page()
    calls = {}
    _install_backend(page, _BASE_ME, calls)

    page.goto("http://test.local/chat")
    page.click("#btn-admin")

    notice = page.locator("#visitor-permission-readonly-hint")
    assert notice.is_visible()
    assert "관리자" in notice.inner_text()
    assert page.locator("#btn-manage-visitor-access").is_hidden()
    context.close()
