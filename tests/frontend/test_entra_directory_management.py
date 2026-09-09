"""Browser contracts for Entra directory management before and after first login."""

import json
from urllib.parse import parse_qs, urlsplit

import pytest
from playwright.sync_api import expect

from test_db_team_visitor_access import _BASE_ME, _install_backend, _url_path, browser


DIRECTORY_USERS = [
    {
        "id": 17, "username": "finance.user", "display_name": "Finance User",
        "email": "finance@example.com", "department": "Craver_Accounts > Users > 브랜드부문 > 재무팀",
        "can_view_fi": True, "can_view_visitor_analytics": True, "user_id": 74,
        "group_names": "SK", "entra_linked": True, "identity_source": "entra",
        "last_signin_at": "2026-09-08T09:00:00+09:00",
    },
    {
        "id": 18, "username": "new.employee", "display_name": "New Employee",
        "email": "new@example.com", "department": "운영본부 > 신규팀",
        "can_view_fi": False, "can_view_visitor_analytics": False, "user_id": None,
        "group_names": None, "entra_linked": True, "identity_source": "entra",
        "last_signin_at": None,
    },
    {
        "id": 19, "username": "legacy.user", "display_name": "Legacy User",
        "email": "legacy@example.com", "department": "해외사업팀",
        "can_view_fi": False, "can_view_visitor_analytics": False, "user_id": 79,
        "group_names": "DD", "entra_linked": False, "identity_source": "legacy",
        "last_signin_at": None,
    },
]


def _install_directory_backend(page, calls, sync_status=200, sync_body=None, directory_users=None):
    _install_backend(page, dict(_BASE_ME, role="admin"), calls)
    users = DIRECTORY_USERS if directory_users is None else directory_users

    def handler(route):
        request = route.request
        path = _url_path(request.url)
        calls.setdefault("admin_requests", []).append((request.method, path))
        if path == "/api/admin/directory/sync":
            route.fulfill(
                status=sync_status, content_type="application/json",
                body=json.dumps(sync_body if sync_body is not None else {"ok": True, "synced_users": 3}),
            )
        elif path == "/api/admin/directory/users":
            calls["directory_reads"] = calls.get("directory_reads", 0) + 1
            calls.setdefault("directory_urls", []).append(request.url)
            route.fulfill(status=200, content_type="application/json", body=json.dumps(users))
        elif path == "/api/admin/directory/stats":
            calls["stats_reads"] = calls.get("stats_reads", 0) + 1
            route.fulfill(status=200, content_type="application/json", body=json.dumps({
                "total_ad_users": 3, "assigned_users": 2, "unassigned_users": 1,
                "fi_allowed_users": 1, "total_groups": 2,
            }))
        elif path == "/api/admin/directory/departments":
            calls["department_reads"] = calls.get("department_reads", 0) + 1
            route.fulfill(status=200, content_type="application/json", body=json.dumps([
                {"department": user["department"], "cnt": 1} for user in DIRECTORY_USERS
            ]))
        elif path == "/api/admin/groups":
            calls["group_reads"] = calls.get("group_reads", 0) + 1
            route.fulfill(status=200, content_type="application/json", body=json.dumps([
                {"id": 1, "name": "SK", "member_count": 1, "brand_filter": "SK,CL,CBT", "description": ""},
                {"id": 2, "name": "DD", "member_count": 1, "brand_filter": "UM", "description": ""},
            ]))
        elif path in (
            "/api/admin/directory/users/18/fi",
            "/api/admin/directory/users/18/visitor-analytics",
            "/api/admin/groups/1/members",
        ):
            calls.setdefault("permission_updates", []).append((
                request.method, path, json.loads(request.post_data or "{}"),
            ))
            route.fulfill(status=200, content_type="application/json", body='{"ok":true,"added":1,"skipped":0,"total":1}')
        else:
            route.fallback()

    page.route("**/api/admin/**", handler)


def _open_users(page):
    page.goto("http://test.local/chat")
    page.click("#btn-admin")
    expect(page.locator("#admin-dept-filter option")).to_have_count(6)
    page.click('.admin-tab[data-tab="users"]')
    expect(page.locator(".admin-ad-user")).to_have_count(3)


def test_first_login_employees_keep_permission_and_group_assignment_controls(browser):
    context = browser.new_context()
    page = context.new_page()
    calls = {}
    _install_directory_backend(page, calls)
    _open_users(page)

    expect(page.locator('.admin-tab[data-tab="users"]')).to_have_text("셀라 사용자")
    expect(page.locator("#admin-directory-help")).to_contain_text("모든 직원은 Entra ID로 로그인해야 합니다.")
    expect(page.locator("#admin-directory-help")).to_contain_text("기존 계정·권한은 로그인 후 자동 승계됩니다.")
    existing = page.locator(".admin-ad-user").filter(has_text="Finance User")
    pending = page.locator(".admin-ad-user").filter(has_text="New Employee")
    legacy = page.locator(".admin-ad-user").filter(has_text="Legacy User")
    expect(existing.locator(".admin-fi-toggle")).to_be_checked()
    expect(existing.locator(".admin-visitor-toggle")).to_be_checked()
    expect(existing.locator(".admin-directory-state")).to_have_text("Entra 로그인 완료")
    expect(pending.locator(".admin-directory-state")).to_have_text("Entra 로그인 필요")
    expect(legacy.locator(".admin-directory-state")).to_have_text("Entra 로그인 필요")
    expect(page.locator(".admin-directory-state > span")).to_have_count(3)
    expect(page.locator(".admin-ad-reset-pw")).to_have_count(0)
    assert not any("password-reset" in path or "/ad/" in path for _, path in calls["admin_requests"])

    with page.expect_response(lambda response: _url_path(response.url) == "/api/admin/directory/users/18/fi"):
        pending.locator(".admin-fi-toggle").check()
    with page.expect_response(lambda response: _url_path(response.url) == "/api/admin/directory/users/18/visitor-analytics"):
        pending.locator(".admin-visitor-toggle").check()
    page.once("dialog", lambda dialog: dialog.accept("1"))
    with page.expect_response(lambda response: _url_path(response.url) == "/api/admin/groups/1/members"):
        pending.locator(".admin-ad-assign").click()

    assert calls["permission_updates"] == [
        ("PUT", "/api/admin/directory/users/18/fi", {"can_view_fi": True}),
        ("PUT", "/api/admin/directory/users/18/visitor-analytics", {"can_view_visitor_analytics": True}),
        ("POST", "/api/admin/groups/1/members", {"ad_user_ids": [18]}),
    ]
    context.close()


@pytest.mark.parametrize("missing_field", ["entra_linked", "user_id", "last_signin_at"])
def test_directory_requires_an_actual_linked_entra_signin_for_completed_status(browser, missing_field):
    context = browser.new_context()
    page = context.new_page()
    calls = {}
    users = [dict(user) for user in DIRECTORY_USERS]
    users[0][missing_field] = None
    _install_directory_backend(page, calls, directory_users=users)
    _open_users(page)

    existing = page.locator(".admin-ad-user").filter(has_text="Finance User")
    expect(existing.locator(".admin-directory-state")).to_have_text("Entra 로그인 필요")
    expect(existing.locator(".admin-fi-toggle")).to_be_checked()
    expect(existing.locator(".admin-visitor-toggle")).to_be_checked()
    expect(existing.locator(".admin-ad-assign")).to_be_enabled()
    context.close()


@pytest.mark.parametrize("http_status", [200, 503])
def test_failed_entra_sync_keeps_visible_users_and_permissions(browser, http_status):
    context = browser.new_context()
    page = context.new_page()
    calls = {}
    _install_directory_backend(page, calls, sync_status=http_status, sync_body={"ok": False, "error": "Entra 연결 오류"})
    _open_users(page)
    before = page.locator("#admin-user-list").inner_html()
    reads = calls["directory_reads"]

    page.click("#btn-sync-directory")

    expect(page.locator("#admin-directory-status")).to_contain_text("동기화 실패: Entra 연결 오류")
    expect(page.locator("#btn-sync-directory")).to_be_enabled()
    assert page.locator("#admin-user-list").inner_html() == before
    assert calls["directory_reads"] == reads
    context.close()


def test_successful_sync_refreshes_directory_and_preserves_visitor_filter(browser):
    context = browser.new_context()
    page = context.new_page()
    calls = {}
    _install_directory_backend(page, calls)
    _open_users(page)
    with page.expect_response(lambda response: "visitor_only=true" in response.url):
        page.select_option("#admin-group-filter", "visitor_allowed")

    page.click("#btn-sync-directory")

    expect(page.locator("#admin-directory-status")).to_have_text("Entra 동기화 완료 · 3명")
    page.wait_for_function("document.querySelector('#btn-sync-directory').disabled === false")
    expect(page.locator("#admin-group-filter")).to_have_value("visitor_allowed")
    expect(page.locator(".admin-ad-user")).to_have_count(3)
    assert calls["department_reads"] == 2
    assert calls["group_reads"] == 2
    assert calls["stats_reads"] == 2
    assert calls["directory_reads"] == 3
    assert "visitor_only=true" in calls["directory_urls"][-1]
    context.close()


def test_entra_department_hierarchy_supports_bulk_assignment_before_login(browser):
    context = browser.new_context()
    page = context.new_page()
    calls = {}
    _install_directory_backend(page, calls)
    _open_users(page)
    expect(page.locator('#admin-dept-filter option[value="운영본부"]')).to_have_count(1)
    expect(page.locator('#admin-dept-filter option[value="신규팀"]')).to_have_count(1)
    expect(page.locator('#admin-dept-filter option[value="브랜드부문"]')).to_have_count(1)
    assert "Craver_Accounts" not in page.locator("#admin-dept-filter").inner_text()

    page.click('.admin-tab[data-tab="groups"]')
    page.locator('.admin-group-card[data-group-id="1"]').get_by_role("button", name="부서 배정").click()
    expect(page.locator('#modal-top-dept option[value="운영본부"]')).to_have_text("운영본부 (1명)")
    page.select_option("#modal-top-dept", "운영본부")
    expect(page.locator("#sub-dept-items")).to_contain_text("신규팀")
    page.click("#modal-load-users")
    expect(page.locator("#dept-user-items")).to_contain_text("New Employee")
    assert parse_qs(urlsplit(calls["directory_urls"][-1]).query)["dept"] == ["운영본부"]
    page.once("dialog", lambda dialog: dialog.accept())
    with page.expect_response(lambda response: _url_path(response.url) == "/api/admin/groups/1/members"):
        page.click("#modal-ok")
    assert calls["permission_updates"][-1] == ("POST", "/api/admin/groups/1/members", {"ad_user_ids": [18]})
    context.close()


def _install_onboarding_backend(page, me, calls):
    _install_backend(page, me, calls)
    state = {"me": me, "status": 200, "headers": {}}

    def me_handler(route):
        calls["me_reads"] = calls.get("me_reads", 0) + 1
        route.fulfill(status=state["status"], headers=state["headers"], content_type="application/json", body=json.dumps(state["me"]))

    def conversations_handler(route):
        request = route.request
        if request.method != "POST":
            route.fallback()
            return
        calls.setdefault("conversation_writes", []).append(_url_path(request.url))
        if _url_path(request.url).endswith("/messages"):
            calls.setdefault("saved_messages", []).append(json.loads(request.post_data))
        route.fulfill(status=200, content_type="application/json", body='{"id":211,"title":"Test conversation"}')

    def chat_handler(route):
        calls.setdefault("chat_requests", []).append(json.loads(route.request.post_data))
        route.fulfill(status=200, content_type="text/event-stream", body="data: [DONE]\n\n")

    page.route("**/api/auth/me", me_handler)
    page.route("**/api/conversations**", conversations_handler)
    page.route("**/v1/chat/completions", chat_handler)
    return state


def test_new_unassigned_user_sees_notice_and_keeps_profile_access(browser):
    context = browser.new_context()
    page = context.new_page()
    calls = {}
    _install_onboarding_backend(page, dict(_BASE_ME, requires_group_assignment=True), calls)
    page.goto("http://test.local/chat")

    expect(page.locator("#group-assignment-notice")).to_be_visible()
    expect(page.locator("#group-assignment-notice")).to_contain_text("관리자가 데이터 조회 그룹을 배정하면 질문을 이용할 수 있습니다.")
    expect(page.locator("#chat-input")).to_be_disabled()
    expect(page.locator("#btn-send")).to_be_disabled()
    expect(page.locator("#btn-attach")).to_be_disabled()
    expect(page.locator("#btn-logout")).to_be_enabled()
    expect(page.locator("#user-name")).to_have_text(_BASE_ME["name"])

    # An event triggered outside the disabled button must still reach the send guard.
    page.evaluate("""() => {
        const input = document.querySelector('#chat-input');
        input.value = 'Show company sales';
        input.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', bubbles: true}));
    }""")
    expect(page.locator("#chat-input")).to_be_disabled()
    assert calls.get("conversation_writes", []) == []
    assert calls.get("chat_requests", []) == []
    page.click("#btn-briefing-settings")
    expect(page.locator(".briefing-settings-box")).to_be_visible()
    context.close()


@pytest.mark.parametrize("flag", [None, False, 0])
def test_existing_users_without_onboarding_hold_keep_question_access(browser, flag):
    context = browser.new_context()
    page = context.new_page()
    calls = {}
    me = dict(_BASE_ME)
    if flag is not None:
        me["requires_group_assignment"] = flag
    _install_onboarding_backend(page, me, calls)
    page.goto("http://test.local/chat")
    expect(page.locator("#user-name")).to_have_text(_BASE_ME["name"])
    expect(page.locator("#group-assignment-notice")).to_be_hidden()
    expect(page.locator("#chat-input")).to_be_enabled()
    page.fill("#chat-input", "Show company sales")
    expect(page.locator("#btn-send")).to_be_enabled()
    page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
    assert calls["me_reads"] == 1
    context.close()


@pytest.mark.parametrize("promoted_after_login", [False, True])
def test_admin_is_exempt_from_stored_group_assignment_hold(browser, promoted_after_login):
    context = browser.new_context()
    page = context.new_page()
    calls = {}
    me = dict(_BASE_ME, role="user" if promoted_after_login else "admin", requires_group_assignment=True)
    state = _install_onboarding_backend(page, me, calls)
    page.goto("http://test.local/chat")
    expect(page.locator("#user-name")).to_have_text(_BASE_ME["name"])
    if promoted_after_login:
        expect(page.locator("#chat-input")).to_be_disabled()
        state["me"] = dict(me, role="admin")
        page.click("#btn-check-group-assignment")

    expect(page.locator("#group-assignment-notice")).to_be_hidden()
    expect(page.locator("#chat-input")).to_be_enabled()
    expect(page.locator("#admin-btn-wrap")).to_be_visible()
    page.fill("#chat-input", "Show company sales")
    expect(page.locator("#btn-send")).to_be_enabled()
    context.close()


def test_returning_to_tab_unlocks_assigned_user_with_fresh_brand_scope(browser):
    context = browser.new_context()
    page = context.new_page()
    calls = {}
    state = _install_onboarding_backend(page, dict(_BASE_ME, requires_group_assignment=True), calls)
    page.goto("http://test.local/chat")
    expect(page.locator("#chat-input")).to_be_disabled()

    state["me"] = dict(_BASE_ME, requires_group_assignment=False, my_brand_filter="SK,CL,CBT")
    with page.expect_response(lambda response: _url_path(response.url) == "/api/auth/me"):
        page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
    expect(page.locator("#group-assignment-notice")).to_be_hidden()
    expect(page.locator("#chat-input")).to_be_enabled()
    expect(page.locator("#btn-attach")).to_be_enabled()
    page.fill("#chat-input", "Show company sales")
    with page.expect_response(lambda response: _url_path(response.url) == "/v1/chat/completions"):
        page.click("#btn-send")
    assert calls["chat_requests"][0]["brand_filter"] == "SK,CL,CBT"
    context.close()


@pytest.mark.parametrize("response_status, response_body", [(503, {"detail": "unavailable"}), (200, {})])
def test_failed_or_incomplete_assignment_refresh_keeps_new_user_on_hold(browser, response_status, response_body):
    context = browser.new_context()
    page = context.new_page()
    calls = {}
    state = _install_onboarding_backend(page, dict(_BASE_ME, requires_group_assignment=True), calls)
    page.goto("http://test.local/chat")
    expect(page.locator("#group-assignment-notice")).to_be_visible()
    state.update(status=response_status, me=response_body)

    page.click("#btn-check-group-assignment")

    expect(page.locator("#group-assignment-status")).to_contain_text("배정 상태를 확인하지 못했습니다.")
    expect(page.locator("#chat-input")).to_be_disabled()
    expect(page.locator("#btn-send")).to_be_disabled()
    expect(page.locator("#btn-check-group-assignment")).to_be_enabled()
    assert calls.get("chat_requests", []) == []
    context.close()


def _install_login_page(page, calls):
    def handler(route):
        calls["login_visits"] = calls.get("login_visits", 0) + 1
        route.fulfill(status=200, content_type="text/html", body="<h1>Entra login</h1>")

    page.route("**/login", handler)


def _marked_login_required(route):
    route.fulfill(status=401, headers={"X-Cella-Auth": "login-required"},
                  content_type="application/json", body='{"detail":"Entra login required"}')


def test_marked_session_failures_redirect_once_and_restore_the_same_users_question(browser):
    context = browser.new_context()
    page = context.new_page()
    calls = {}
    _install_onboarding_backend(page, dict(_BASE_ME), calls)
    _install_login_page(page, calls)
    page.route("**/api/session-probe/*", _marked_login_required)
    page.goto("http://test.local/chat")
    expect(page.locator("#user-name")).to_have_text(_BASE_ME["name"])
    question = "@@보고서 이번 달 매출을 알려줘"
    page.fill("#chat-input", question)

    page.evaluate("""() => {
        void Promise.allSettled([fetch('/api/session-probe/one'), fetch('/api/session-probe/two')]);
    }""")

    page.wait_for_url("http://test.local/login")
    assert calls["login_visits"] == 1
    draft = page.evaluate("JSON.parse(sessionStorage.getItem('cella:reauth-question'))")
    assert draft == {"userId": _BASE_ME["id"], "text": question}

    page.goto("http://test.local/chat")
    expect(page.locator("#chat-input")).to_have_value(question)
    assert page.evaluate("sessionStorage.getItem('cella:reauth-question')") is None
    assert calls.get("chat_requests", []) == []
    assert calls.get("conversation_writes", []) == []
    context.close()


def test_initial_marked_me_failure_redirects_once(browser):
    context = browser.new_context()
    page = context.new_page()
    calls = {}
    state = _install_onboarding_backend(page, dict(_BASE_ME), calls)
    state.update(status=401, me={"detail": "Entra login required"}, headers={"X-Cella-Auth": "login-required"})
    _install_login_page(page, calls)

    page.goto("http://test.local/chat")

    page.wait_for_url("http://test.local/login")
    assert calls["login_visits"] == 1
    assert calls.get("conversation_writes", []) == []
    context.close()


def test_reauth_question_is_not_restored_to_a_different_user(browser):
    context = browser.new_context()
    page = context.new_page()
    calls = {}
    state = _install_onboarding_backend(page, dict(_BASE_ME), calls)
    page.goto("http://test.local/chat")
    expect(page.locator("#user-name")).to_have_text(_BASE_ME["name"])
    page.evaluate("draft => sessionStorage.setItem('cella:reauth-question', JSON.stringify(draft))",
                  {"userId": _BASE_ME["id"], "text": "Previous user's question"})
    state["me"] = dict(_BASE_ME, id=53, name="Different User")

    page.goto("http://test.local/chat")

    expect(page.locator("#user-name")).to_have_text("Different User")
    expect(page.locator("#chat-input")).to_have_value("")
    assert page.evaluate("sessionStorage.getItem('cella:reauth-question')") is None
    context.close()


@pytest.mark.parametrize("denied_stage", ["conversation", "user_message", "chat_stream"])
def test_marked_401_stops_question_pipeline_without_saving_an_empty_answer(browser, denied_stage):
    context = browser.new_context()
    page = context.new_page()
    calls = {}
    _install_onboarding_backend(page, dict(_BASE_ME), calls)
    _install_login_page(page, calls)
    denied_path = {
        "conversation": "/api/conversations",
        "user_message": "/api/conversations/211/messages",
        "chat_stream": "/v1/chat/completions",
    }[denied_stage]

    def deny_post(route):
        if route.request.method == "POST":
            calls["denied_requests"] = calls.get("denied_requests", 0) + 1
            _marked_login_required(route)
        else:
            route.fallback()

    page.route("**" + denied_path, deny_post)
    page.goto("http://test.local/chat")
    expect(page.locator("#user-name")).to_have_text(_BASE_ME["name"])
    question = "@@보고서 회사 매출을 알려줘"
    page.fill("#chat-input", question)
    page.click("#btn-send")

    page.wait_for_url("http://test.local/login")
    assert calls["login_visits"] == 1
    assert calls["denied_requests"] == 1
    assert [message["role"] for message in calls.get("saved_messages", [])] == (["user"] if denied_stage == "chat_stream" else [])
    assert calls.get("chat_requests", []) == []
    assert page.evaluate("JSON.parse(sessionStorage.getItem('cella:reauth-question')).text") == question
    context.close()


@pytest.mark.parametrize("path, method", [
    ("/auth/google/status", "GET"),
    ("/auth/google/status/", "GET"),
    ("/auth/google/login", "GET"),
    ("/auth/google/revoke", "POST"),
    ("/safety/status", "GET"),
    ("/admin/maintenance/status", "GET"),
])
def test_marked_private_service_session_failure_redirects_to_login(browser, path, method):
    context = browser.new_context()
    page = context.new_page()
    calls = {}
    _install_onboarding_backend(page, dict(_BASE_ME), calls)
    _install_login_page(page, calls)
    page.goto("http://test.local/chat")
    expect(page.locator("#user-name")).to_have_text(_BASE_ME["name"])
    page.route("**" + path, _marked_login_required)

    page.evaluate("""({path, method}) => {
        void fetch(path, {method}).catch(() => {});
    }""", {"path": path, "method": method})

    page.wait_for_url("http://test.local/login")
    assert calls["login_visits"] == 1
    assert calls.get("conversation_writes", []) == []
    context.close()


@pytest.mark.parametrize("url, status, marker", [
    ("/api/gws/probe", 401, False),
    ("/auth/google/status", 401, False),
    ("/auth/google/revoke", 401, False),
    ("/auth/google/callback", 401, True),
    ("/api/auth/google/callback", 401, True),
    ("/auth/google/probe", 401, True),
    ("https://graph.example.test/v1.0/me", 401, True),
    ("/api/permissions/probe", 403, True),
])
def test_other_service_failures_do_not_log_out_the_cella_user(browser, url, status, marker):
    context = browser.new_context()
    page = context.new_page()
    calls = {}
    _install_onboarding_backend(page, dict(_BASE_ME), calls)
    _install_login_page(page, calls)
    page.goto("http://test.local/chat")
    expect(page.locator("#user-name")).to_have_text(_BASE_ME["name"])

    def handler(route):
        headers = {"Access-Control-Allow-Origin": "http://test.local", "Access-Control-Expose-Headers": "X-Cella-Auth"}
        if marker:
            headers["X-Cella-Auth"] = "login-required"
        route.fulfill(status=status, headers=headers, content_type="application/json", body='{"detail":"service authorization required"}')

    page.route(url if url.startswith("https:") else "**" + url, handler)
    result = page.evaluate("async url => (await fetch(url)).status", url)

    assert result == status
    assert page.url == "http://test.local/chat"
    assert calls.get("login_visits", 0) == 0
    context.close()


def test_unmarked_failed_chat_response_uses_the_existing_error_ui(browser):
    context = browser.new_context()
    page = context.new_page()
    calls = {}
    _install_onboarding_backend(page, dict(_BASE_ME), calls)
    _install_login_page(page, calls)
    page.route("**/v1/chat/completions", lambda route: route.fulfill(
        status=401, content_type="application/json", body='{"detail":"upstream authorization failed"}',
    ))
    page.goto("http://test.local/chat")
    expect(page.locator("#user-name")).to_have_text(_BASE_ME["name"])
    page.fill("#chat-input", "Show company sales")
    with page.expect_response(lambda response: _url_path(response.url) == "/v1/chat/completions"):
        page.click("#btn-send")

    expect(page.locator(".message-assistant")).to_contain_text("HTTP 401")
    assert page.url == "http://test.local/chat"
    assert calls.get("login_visits", 0) == 0
    context.close()
