"""The real notification drawer must explain failed loads without marking them read.

Only network responses are controlled. Markup, scripts, CSS, clicks, navigation,
and timers are the same browser behavior users get from chat.html.
"""
import json
import mimetypes
from collections import deque
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import expect, sync_playwright


ROOT = Path(__file__).resolve().parents[2]
ME = {
    "id": 42, "email": "notifications@example.invalid", "name": "알림 검사",
    "department": "OP", "role": "user", "can_view_fi": False,
    "must_change_password": False, "allowed_models": ["skin1004-Analysis"],
    "brand_filters": [], "my_brand_filter": None,
}
PAYLOAD = {
    "unseen": 2,
    "items": [
        {"type": "feedback", "feedback_id": 172, "title": "예상 수치를 고쳐 주세요",
         "has_comment": True, "status": "done", "status_label": "해결됨",
         "note": "미래 날짜를 제외하고 실제 데이터가 있는 날짜로 계산하도록 고쳤습니다.",
         "created_at": "2026-09-08T16:00:00", "handled_at": "2026-09-09T09:00:00",
         "seen": False, "url": ""},
        {"type": "briefing", "title": "이번 주 지표", "note": "주간 매출 변화를 확인하세요.",
         "follow_up": "미국 최근 2주 매출 추이 보여줘", "created_at": "2026-09-09T08:00:00",
         "seen": False, "url": ""},
    ],
}


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as playwright:
        instance = playwright.chromium.launch()
        yield instance
        instance.close()


class NotificationPage:
    def __init__(self, page):
        self.page = page
        self.actions = deque()
        self.pending = deque()
        self.viewed = []
        self.gets = 0
        page.clock.install()
        page.add_init_script("localStorage.setItem('skin1004-cella-origin-invited-v1', '1');")
        page.route("**/*", self.route)
        page.goto("http://notifications.test/chat")
        expect(page.locator("#notif-badge")).to_have_text("2")

    @staticmethod
    def fulfill(route, data):
        route.fulfill(status=200, content_type="application/json", body=json.dumps(data))

    def route(self, route):
        req = route.request
        url = urlsplit(req.url)
        path = url.path
        if url.netloc == "notifications.test" and path == "/chat":
            route.fulfill(status=200, content_type="text/html", body=(ROOT / "app/frontend/chat.html").read_bytes())
            return
        if path == "/login":
            route.fulfill(status=200, content_type="text/html", body="<p>로그인</p>")
            return
        if url.netloc == "notifications.test" and path.startswith(("/static/", "/frontend/")):
            asset = ROOT / "app" / path.lstrip("/")
            if asset.is_file():
                route.fulfill(status=200, content_type=mimetypes.guess_type(path)[0] or "application/octet-stream",
                              body=asset.read_bytes())
                return
        if path == "/api/notifications":
            self.gets += 1
            action = self.actions.popleft() if self.actions else PAYLOAD
            if action == "pending":
                self.pending.append(route)
            elif action == "network":
                route.abort("failed")
            elif action == "json":
                route.fulfill(status=200, content_type="application/json", body="not-json")
            elif action in ("http", "401", "401-header"):
                route.fulfill(status=401 if action.startswith("401") else 503,
                              headers={"X-Cella-Auth": "login-required"} if action == "401-header" else {},
                              content_type="application/json", body='{"detail":"Unavailable"}')
            else:
                self.fulfill(route, action)
            return
        if path == "/api/notifications/viewed":
            self.viewed.append(req.method)
            self.fulfill(route, {"marked": 1})
            return
        if path == "/api/auth/me":
            data = ME
        elif path in ("/api/conversations", "/api/datasources", "/api/saved-questions",
                      "/api/conversations/feedback/replies"):
            data = []
        elif req.resource_type == "script":
            route.fulfill(status=200, content_type="application/javascript", body="")
            return
        elif req.resource_type == "stylesheet":
            route.fulfill(status=200, content_type="text/css", body="")
            return
        else:
            data = {}
        self.fulfill(route, data)

    def open(self, action=PAYLOAD):
        self.actions.append(action)
        self.page.locator("#btn-notifications").click()

    def background_poll(self):
        with self.page.expect_request(lambda req: urlsplit(req.url).path == "/api/notifications"):
            self.page.clock.fast_forward(300001)

    def settle(self):
        # Let fetch continuations and their outgoing viewed request reach the route.
        self.page.evaluate("() => new Promise(resolve => setTimeout(resolve, 0))")


@pytest.fixture
def ui(browser):
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    page = context.new_page()
    page.set_default_timeout(3000)
    instance = NotificationPage(page)
    yield instance
    while instance.pending:
        instance.pending.popleft().abort("aborted")
    page.unroute_all(behavior="wait")
    context.close()


def test_loading_is_visible_before_the_response(ui):
    ui.open("pending")
    expect(ui.page.locator("#notif-items")).to_contain_text("불러오는 중")


def test_loading_does_not_mark_unseen_briefings_as_viewed(ui):
    ui.open("pending")
    ui.settle()
    assert ui.viewed == []


@pytest.mark.parametrize("failure", ["http", "network", "json", {"unseen": 2, "items": {"unexpected": True}}])
def test_failed_loading_explains_the_problem_and_can_retry(ui, failure):
    ui.open(failure)
    box = ui.page.locator("#notif-items")
    expect(box).to_contain_text("알림을 불러오지 못했습니다")
    assert ui.viewed == []
    box.get_by_role("button", name="다시 시도").click()
    expect(box.locator(".notif-item")).to_have_count(2)
    ui.settle()
    assert ui.viewed == ["POST"]


@pytest.mark.parametrize("failure", ["401", "401-header"])
def test_expired_notification_session_reaches_login(ui, failure):
    ui.page.locator("#chat-input").fill("인증 후 이어서 보낼 질문")
    ui.open(failure)
    expect(ui.page).to_have_url("http://notifications.test/login")
    assert ui.viewed == []
    saved = ui.page.evaluate("sessionStorage.getItem('cella:reauth-question')")
    assert json.loads(saved) == {"userId": 42, "text": "인증 후 이어서 보낼 질문"}


def test_success_displays_feedback_note_and_keeps_briefing_follow_up(ui):
    ui.open()
    box = ui.page.locator("#notif-items")
    expect(box.locator(".notif-item")).to_have_count(2)
    expect(box).to_contain_text("미래 날짜를 제외하고 실제 데이터가 있는 날짜로 계산하도록 고쳤습니다.")
    ui.settle()
    assert ui.viewed == ["POST"]
    box.get_by_role("button", name="미국 최근 2주 매출 추이 보여줘 →").click()
    expect(ui.page.locator("#chat-input")).to_have_value("미국 최근 2주 매출 추이 보여줘")
    expect(ui.page.locator("#notif-drawer")).to_have_class("closed")


def test_successful_empty_list_explains_that_there_are_no_notifications(ui):
    ui.open({"unseen": 0, "items": []})
    expect(ui.page.locator("#notif-items")).to_contain_text("새 알림이 없습니다")
    expect(ui.page.locator("#notif-badge")).to_be_hidden()


def test_closing_during_loading_does_not_mark_the_late_result_viewed(ui):
    ui.open("pending")
    ui.page.locator("#notif-drawer-close").click()
    ui.fulfill(ui.pending.popleft(), PAYLOAD)
    ui.settle()
    assert ui.viewed == []
    expect(ui.page.locator("#notif-drawer")).to_have_class("closed")


def test_late_failure_from_a_closed_drawer_cannot_replace_reopened_contents(ui):
    ui.open("pending")
    ui.page.locator("#notif-drawer-close").click()
    ui.open()
    expect(ui.page.locator("#notif-items .notif-item")).to_have_count(2)
    ui.pending.popleft().fulfill(status=503, body="Unavailable")
    ui.settle()
    expect(ui.page.locator("#notif-items .notif-item")).to_have_count(2)
    assert ui.viewed == ["POST"]


def test_background_badge_refresh_does_not_erase_a_failed_drawer(ui):
    ui.open("http")
    expect(ui.page.locator("#notif-items")).to_contain_text("알림을 불러오지 못했습니다")
    ui.background_poll()
    expect(ui.page.locator("#notif-items").get_by_role("button", name="다시 시도")).to_be_visible()
    assert ui.viewed == []


def test_background_badge_refresh_does_not_replace_a_loading_drawer(ui):
    ui.open("pending")
    ui.background_poll()
    expect(ui.page.locator("#notif-items")).to_contain_text("불러오는 중")
    assert ui.viewed == []
    ui.fulfill(ui.pending.popleft(), PAYLOAD)
    expect(ui.page.locator("#notif-items .notif-item")).to_have_count(2)
    ui.settle()
    assert ui.viewed == ["POST"]
