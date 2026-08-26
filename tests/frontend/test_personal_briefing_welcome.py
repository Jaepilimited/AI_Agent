"""Browser-level regressions for the welcome-only personal briefing."""

from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "app/frontend/personal-briefing.js"
STYLE = ROOT / "app/static/style.css"
CHAT_HTML = ROOT / "app/frontend/chat.html"


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


def test_welcome_briefing_heading_is_today(page):
    page.set_content(CHAT_HTML.read_text(encoding="utf-8"))

    assert page.locator("#personal-briefing-title").inner_text() == "Today"


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
                    "start": "2026-08-26T10:00:00+09:00",
                    "end": "2026-08-26T11:00:00+09:00",
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


def test_todays_events_belong_to_the_document_not_the_card(page):
    """⛔ 같은 일정을 문서와 카드가 둘 다 그리면 한 화면에서 같은 말을 두 번 한다."""
    page.set_content(
        '<section id="personal-briefing"></section><textarea id="chat-input"></textarea>'
    )
    page.add_script_tag(path=str(SCRIPT))
    payload = _fixture()
    payload["calendar"]["items"][0]["start"] = "2026-08-25T10:00:00+09:00"
    payload["calendar"]["items"][0]["end"] = "2026-08-25T11:00:00+09:00"
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
    titles = page.locator(".personal-briefing-card-title").all_inner_texts()
    assert not any("내일부터" in text for text in titles), titles


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
    # 카드 항목은 Today 일정과 같은 시간축 행이다 — 시각은 왼쪽 축으로 빠지고
    # 제목만 본문에 남는다. 긴 제목의 hover 텍스트는 그대로 지켜야 한다.
    row = page.locator(".personal-briefing-card .briefing-doc-row").first
    head = row.locator(".briefing-doc-row-head")
    assert head.inner_text() == "이번 주 주간 회의 전체 제목"
    assert head.get_attribute("title") == "이번 주 주간 회의 전체 제목"
    assert row.locator(".briefing-doc-time b").inner_text() == "8/26(수)"
    assert row.locator(".briefing-doc-time span").inner_text() == "10:00"


def test_all_day_calendar_item_is_labeled_as_all_day(page):
    page.set_content('<section id="personal-briefing"></section><textarea id="chat-input"></textarea>')
    page.add_script_tag(path=str(SCRIPT))
    payload = _fixture()
    payload["calendar"]["items"][0].update(
        start="2026-08-26", end="2026-08-27", all_day=True, title="전사 휴무일",
    )
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

    row = page.locator(".personal-briefing-card .briefing-doc-row").first
    assert row.locator(".briefing-doc-row-head").inner_text() == "전사 휴무일"
    assert row.locator(".briefing-doc-time span").inner_text() == "종일"


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


def test_timeout_error_envelope_does_not_erase_same_day_cached_cards(page):
    page.set_content('<section id="personal-briefing"></section><textarea id="chat-input"></textarea>')
    page.add_script_tag(path=str(SCRIPT))
    cached = _fixture(status="stale", subject="저장된 메일", needs_refresh=True)
    timeout = _fixture(status="error", subject="", needs_refresh=False)
    timeout["generated_at"] = ""
    timeout["priorities"] = []
    timeout["calendar"] = {"status": "error", "items": [], "truncated": False, "error_code": "google_timeout"}
    timeout["mail"] = {
        "status": "error", "count_label": "0건", "unread": 0, "summary": "",
        "action_candidates": [], "items": [], "truncated": False, "error_code": "google_timeout",
    }
    timeout["business"] = {"status": "error", "item": None}
    page.evaluate(
        """async ([cached, timeout]) => {
          let calls = 0;
          const fetchImpl = async () => ({ok: true, json: async () => (++calls === 1 ? cached : timeout)});
          const controller = CellaPersonalBriefing.create({
            root: document.querySelector('#personal-briefing'),
            input: document.querySelector('#chat-input'), connect: () => {}, fetchImpl
          });
          await controller.load();
        }""",
        [cached, timeout],
    )

    assert "저장된 메일" in page.locator("#personal-briefing").inner_text()
    assert "지난 정보" in page.locator("#personal-briefing").inner_text()


def test_disconnected_card_reconnects_without_email_parameter(page):
    page.set_content('<section id="personal-briefing"></section><textarea id="chat-input"></textarea>')
    page.add_script_tag(path=str(SCRIPT))
    disconnected = _fixture()
    disconnected["google"] = {"connected": False, "account": ""}
    disconnected["calendar"] = {"status": "disconnected", "items": [], "truncated": False}
    disconnected["mail"] = {
        "status": "disconnected", "count_label": "0건", "unread": 0, "summary": "",
        "action_candidates": [], "items": [], "truncated": False,
    }
    page.evaluate(
        """async payload => {
          window.connectCalls = [];
          window.googleStates = [];
          const fetchImpl = async () => ({ok: true, json: async () => payload});
          const controller = CellaPersonalBriefing.create({
            root: document.querySelector('#personal-briefing'),
            input: document.querySelector('#chat-input'),
            connect: (...args) => window.connectCalls.push(args),
            onGoogleState: connected => window.googleStates.push(connected), fetchImpl
          });
          await controller.load();
        }""",
        disconnected,
    )
    page.locator(".personal-briefing-connect").click()
    assert page.evaluate("window.connectCalls") == [[]]
    assert page.evaluate("window.googleStates")[-1] is False

    source = (ROOT / "app/frontend/chat.js").read_text(encoding="utf-8")
    assert 'window.open("/auth/google/login", "gws_auth"' in source
    assert "/auth/google/login?" not in source


def test_oauth_expired_stale_card_also_offers_reconnect(page):
    page.set_content('<section id="personal-briefing"></section><textarea id="chat-input"></textarea>')
    page.add_script_tag(path=str(SCRIPT))
    expired = _fixture(status="stale", subject="저장본", needs_refresh=False)
    expired["google"] = {"connected": False, "account": ""}
    expired["mail"]["error_code"] = "oauth_expired"
    page.evaluate(
        """async payload => {
          const controller = CellaPersonalBriefing.create({
            root: document.querySelector('#personal-briefing'),
            input: document.querySelector('#chat-input'), connect: () => {},
            fetchImpl: async () => ({ok: true, json: async () => payload})
          });
          await controller.load();
        }""",
        expired,
    )

    assert page.locator(".personal-briefing-connect").count() == 1


def test_latest_request_wins_after_account_invalidation(page):
    """A late prior-account response cannot redraw content after revoke/switch."""
    page.set_content('<section id="personal-briefing"></section><textarea id="chat-input"></textarea>')
    page.add_script_tag(path=str(SCRIPT))
    old_account = _fixture(subject="이전 계정 비밀 메일", needs_refresh=False)
    disconnected = _fixture(needs_refresh=False)
    disconnected["google"] = {"connected": False, "account": ""}
    disconnected["calendar"] = {"status": "disconnected", "items": [], "truncated": False, "error_code": "oauth_missing"}
    disconnected["mail"] = {
        "status": "disconnected", "count_label": "0건", "unread": 0, "summary": "",
        "action_candidates": [], "items": [], "truncated": False, "error_code": "oauth_missing",
    }
    page.evaluate(
        """async ([oldAccount, disconnected]) => {
          let resolveOld;
          let calls = 0;
          const oldResponse = new Promise(resolve => { resolveOld = resolve; });
          const fetchImpl = () => {
            calls += 1;
            if (calls === 1) return oldResponse;
            return Promise.resolve({ok: true, json: async () => disconnected});
          };
          const controller = CellaPersonalBriefing.create({
            root: document.querySelector('#personal-briefing'),
            input: document.querySelector('#chat-input'), connect: () => {}, fetchImpl
          });
          const priorLoad = controller.load();
          await Promise.resolve();
          controller.invalidate();
          await controller.load();
          resolveOld({ok: true, json: async () => oldAccount});
          await priorLoad;
        }""",
        [old_account, disconnected],
    )

    text = page.locator("#personal-briefing").inner_text()
    assert "이전 계정 비밀 메일" not in text
    assert "Google 연결" in text

    source = (ROOT / "app/frontend/chat.js").read_text(encoding="utf-8")
    assert source.count("personalBriefingController.invalidate()") >= 2


def test_existing_conversation_still_hides_welcome():
    source = (ROOT / "app/frontend/chat.js").read_text(encoding="utf-8")
    briefing_source = SCRIPT.read_text(encoding="utf-8")
    block = source[source.index("async function loadConversation") : source.index("async function saveMessage")]
    assert 'chatWelcome.style.display = "none"' in block
    assert "personalBriefingController.show()" not in block
    assert "localStorage" not in briefing_source
    assert "innerHTML" not in briefing_source


# ── 메일 읽음/안읽음 · 새로고침 버튼 (2026-08-26 요청) ───────────────────────

def _read(rel: str) -> str:
    from pathlib import Path

    return (Path(__file__).resolve().parent.parent.parent / rel).read_text(encoding="utf-8")


def test_mail_rows_show_whether_they_were_read():
    """⛔ `unread` 값은 처음부터 파이프라인에 있었는데 **화면에만 없었다** —
       메일이 전부 같은 굵기로 보여서 무엇을 아직 안 봤는지 알 수 없었다.

    ⚠️ 표시는 행 클래스로만 한다. 좁은 칸에 배지 글자를 넣으면 제목을 밀어낸다
       (`OP` → `O` 사고와 같은 부류).
    """
    js = _read("app/frontend/personal-briefing.js")
    assert "mail-unread" in js and "mail-read" in js
    assert "item.unread" in js

    # ⛔ 칸을 나눈다 (2026-08-26 추가 요청). 한 목록에 굵기로만 구분하면 사이사이
    #    읽은 메일이 끼어 **아직 볼 것이 몇 개인지 세어야** 한다.
    assert "mailGroup" in js
    group = js.split("function mailGroup", 1)[1].split(chr(10) + "  }" + chr(10), 1)[0]
    assert "if (!rows.length) return;" in group, "빈 칸을 만들면 자리만 먹는다"
    order = js.split("function renderMailSection", 1)[1]
    assert order.index('"안읽음"') < order.index('"읽음"'), "안읽음이 먼저다"

    # ⛔ 안전망 카드(문서가 비었을 때만 뜬다)도 같은 순서·같은 표시를 쓴다 —
    #    첫 화면 어디서든 같은 어법이어야 한다 (2026-08-26).
    cards = js.split("function renderCards", 1)[1]
    assert "item.unread; })" in cards, "카드가 안 읽은 것을 위로 올리지 않는다"
    assert "mail-unread" in cards, "카드 행에 읽음 표시가 없다"
    # ⚠️ 카드는 문서가 메일을 못 실었을 때만 만든다 — 아니면 목록이 두 벌이 된다
    assert "docMail.length ? [] : safeItems(data.mail)" in cards

    css = _read("app/static/style.css")
    assert ".briefing-doc-row.mail-unread .briefing-doc-row-head" in css
    assert ".briefing-doc-row.mail-read .briefing-doc-row-head" in css


def test_today_has_a_refresh_button_that_always_refetches():
    """⛔ `load()` 는 서버가 "갱신이 필요하다"(`needs_refresh`)고 할 때만 새로
       가져온다. 그 경로에 버튼을 걸면 눌러도 캐시가 그대로 보여 **고장 난 것처럼**
       보인다 — 버튼은 무조건 새로 가져오는 경로여야 한다.
    """
    html = _read("app/frontend/chat.html")
    assert 'id="personal-briefing-refresh"' in html

    js = _read("app/frontend/personal-briefing.js")
    assert "refreshNow" in js and "refresh: refreshNow" in js
    body = js.split("async function refreshNow", 1)[1].split("function ", 1)[0]
    assert "/api/personal-briefing/refresh" in body
    assert "needs_refresh" not in body, "캐시 조건을 다시 보면 버튼이 안 먹는다"
    # 연타 방지 + 실패를 말한다 (조용히 넘기면 새로 받은 줄 안다)
    assert "refreshing" in body and "markRefreshFailed" in body

    chat = _read("app/frontend/chat.js")
    assert "personal-briefing-refresh" in chat and ".refresh()" in chat

    # ⛔ 서버에도 같은 게이트가 있었다 — `refresh_for_user()` 는 `force` 없이 부르면
    #    캐시(CACHE_TTL 10분)를 그대로 돌려준다. 버튼이 그 경로를 써서 "눌러도
    #    시간이 안 바뀐다" 는 제보를 받았다 (2026-08-26). **양쪽 다** 강제해야 한다.
    assert "force=1" in js, "클라이언트가 강제 갱신을 요청하지 않는다"
    api = _read("app/api/personal_briefing_api.py")
    assert "force: bool = False" in api, "엔드포인트에 force 가 없다"
    assert "_tracked_refresh(user, now, force=force)" in api
    assert "refresh_for_user(user, now=now, force=force)" in api


def test_cut_unread_mail_is_disclosed_on_screen():
    """⛔ 자리가 모자라 잘렸는데 목록이 멀쩡히 보이면 **그게 전부인 줄 안다.**
       조용히 자르는 것이 이 화면에서 가장 나쁜 실패다 — 서버가 센 수를 화면이 말한다.
    """
    js = _read("app/frontend/personal-briefing.js")
    assert "mail_omitted_unread" in js
    section = js.split("function renderMailSection", 1)[1].split(chr(10) + "  }" + chr(10), 1)[0]
    assert section.index("mail_omitted_unread") > section.index('"안읽음"'), \
        "안읽음 목록 바로 뒤에 붙어야 무엇이 빠졌는지 이어서 읽힌다"

    server = _read("app/core/work_briefing.py")
    assert '"mail_omitted_unread": omitted_unread' in server
