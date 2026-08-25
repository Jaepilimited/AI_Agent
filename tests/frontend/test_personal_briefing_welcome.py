"""Browser-level regressions for the welcome-only personal briefing."""

from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "app/frontend/personal-briefing.js"
STYLE = ROOT / "app/static/style.css"


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as playwright:
        instance = playwright.chromium.launch()
        yield instance
        instance.close()


@pytest.fixture
def page(browser):
    context = browser.new_context()
    current = context.new_page()
    yield current
    context.close()


def _fixture(status="ready", subject="월별로 보았을 때 B2C 매출 갭이 가장 큰 지역", needs_refresh=False):
    return {
        "enabled": True,
        "for_date": "2026-08-25",
        "generated_at": "2026-08-25T08:30:00+09:00",
        "needs_refresh": needs_refresh,
        "google": {"connected": True, "account": "me@example.com"},
        "priorities": [],
        "calendar": {
            "status": "ready",
            "items": [
                {
                    "id": "e1",
                    "title": "이번 주 주간 회의 전체 제목",
                    "start": "2026-08-25T10:00:00+09:00",
                    "end": "2026-08-25T11:00:00+09:00",
                    "all_day": False,
                    "location": "",
                    "url": "",
                    "ended": False,
                }
            ],
            "truncated": False,
            "error_code": "",
        },
        "mail": {
            "status": status,
            "count_label": "1건",
            "unread": 1,
            "summary": "",
            "action_candidates": [],
            "items": [
                {
                    "id": "m1",
                    "thread_id": "t1",
                    "subject": subject,
                    "from_display": "A",
                    "received_at": "2026-08-25T08:00:00+09:00",
                    "unread": True,
                    "url": "",
                }
            ],
            "truncated": False,
            "error_code": "",
        },
        "business": {"status": "empty", "item": None},
    }


def test_cached_cards_render_and_long_titles_have_hover_text(page):
    page.set_content(
        '<section id="personal-briefing"></section><textarea id="chat-input"></textarea>'
    )
    page.add_script_tag(path=str(SCRIPT))
    payload = _fixture()
    page.evaluate(
        """async payload => {
            const fetchImpl = async () => ({ok: true, json: async () => payload});
            window.controller = CellaPersonalBriefing.create({
              root: document.querySelector('#personal-briefing'),
              input: document.querySelector('#chat-input'), connect: () => {}, fetchImpl
            });
            await window.controller.load();
        }""",
        payload,
    )
    item = page.locator(".personal-briefing-item").first
    assert item.inner_text() == "이번 주 주간 회의 전체 제목"
    assert item.get_attribute("title") == "이번 주 주간 회의 전체 제목"


def test_renderer_treats_google_text_as_text_and_rejects_bad_urls(page):
    page.set_content('<section id="personal-briefing"></section><textarea id="chat-input"></textarea>')
    page.add_script_tag(path=str(SCRIPT))
    result = page.evaluate(
        """() => ({
          js: CellaPersonalBriefing.safeUrl('javascript:alert(1)'),
          evil: CellaPersonalBriefing.safeUrl('https://evil.example/x'),
          insecure: CellaPersonalBriefing.safeUrl('http://mail.google.com/mail/u/0/#all/m1'),
          mail: CellaPersonalBriefing.safeUrl('https://mail.google.com/mail/u/0/#all/m1')
        })"""
    )
    assert result == {
        "js": "",
        "evil": "",
        "insecure": "",
        "mail": "https://mail.google.com/mail/u/0/#all/m1",
    }
    payload = _fixture()
    payload["calendar"]["items"][0]["title"] = "<img src=x onerror=window.xss=1>"
    page.evaluate(
        """async payload => {
          const fetchImpl = async () => ({ok: true, json: async () => payload});
          const controller = CellaPersonalBriefing.create({
            root: document.querySelector('#personal-briefing'),
            input: document.querySelector('#chat-input'), connect: () => {}, fetchImpl
          });
          await controller.load();
        }""",
        payload,
    )
    assert page.locator("#personal-briefing img").count() == 0
    assert page.evaluate("window.xss") is None


def test_stale_get_is_replaced_by_refresh_and_mobile_is_one_column(page):
    page.set_viewport_size({"width": 390, "height": 844})
    page.set_content(
        '<section class="personal-briefing" id="personal-briefing">'
        '<div class="personal-briefing-grid" id="personal-briefing-grid"></div>'
        '</section><textarea id="chat-input"></textarea>'
    )
    page.add_style_tag(path=str(STYLE))
    page.add_script_tag(path=str(SCRIPT))
    stale = _fixture(status="stale", subject="오래된 메일", needs_refresh=True)
    fresh = _fixture(status="ready", subject="최신 메일", needs_refresh=False)
    page.evaluate(
        """async ([stale, fresh]) => {
          let calls = 0;
          const fetchImpl = async () => ({ok: true, json: async () => (++calls === 1 ? stale : fresh)});
          window.controller = CellaPersonalBriefing.create({
            root: document.querySelector('#personal-briefing'),
            input: document.querySelector('#chat-input'), connect: () => {}, fetchImpl
          });
          await window.controller.load();
        }""",
        [stale, fresh],
    )
    assert "최신 메일" in page.locator("#personal-briefing").inner_text()
    columns = page.locator("#personal-briefing-grid").evaluate(
        "el => getComputedStyle(el).gridTemplateColumns"
    )
    assert len(columns.split()) == 1


def test_failed_refresh_keeps_the_cached_cards_visible(page):
    page.set_content('<section id="personal-briefing"></section><textarea id="chat-input"></textarea>')
    page.add_script_tag(path=str(SCRIPT))
    stale = _fixture(status="stale", subject="저장된 메일", needs_refresh=True)
    page.evaluate(
        """async stale => {
          let calls = 0;
          const fetchImpl = async () => {
            calls += 1;
            return calls === 1
              ? {ok: true, json: async () => stale}
              : {ok: false, json: async () => ({})};
          };
          const controller = CellaPersonalBriefing.create({
            root: document.querySelector('#personal-briefing'),
            input: document.querySelector('#chat-input'), connect: () => {}, fetchImpl
          });
          await controller.load();
        }""",
        stale,
    )
    card = page.locator("#personal-briefing")
    assert "저장된 메일" in card.inner_text()
    assert "지난 정보" in card.inner_text()


def test_existing_conversation_still_hides_welcome():
    source = (ROOT / "app/frontend/chat.js").read_text(encoding="utf-8")
    briefing_source = SCRIPT.read_text(encoding="utf-8")
    block = source[source.index("async function loadConversation") : source.index("async function saveMessage")]
    assert 'chatWelcome.style.display = "none"' in block
    assert "personalBriefingController.show()" not in block
    assert "localStorage" not in briefing_source
    assert "innerHTML" not in briefing_source
