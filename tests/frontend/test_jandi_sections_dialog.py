"""잔디 설정 다이얼로그의 '받을 항목' — 실제 브라우저에서 끄고 저장되는지.

여기서 잡으려는 것도 **에러 없이 잘못 보이는** 종류다: 체크를 꺼도 payload 에
안 실리면 화면은 "저장됨" 이라고 말하고 잔디는 그대로 온다.
"""

from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "app/frontend/personal-briefing.js"
STYLE = ROOT / "app/static/style.css"

SECTIONS = [
    {"key": "meetings", "label": "오늘의 일정", "group": "브리핑", "enabled": True},
    {"key": "mail", "label": "수신 메일", "group": "브리핑", "enabled": False},
    {"key": "actions", "label": "우선순위 Action Item", "group": "브리핑",
     "enabled": True},
    {"key": "report_share", "label": "보고서 공유 알림", "group": "알림",
     "enabled": True},
]


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


def _open(page, state):
    page.set_content('<section id="personal-briefing"></section>')
    page.add_style_tag(path=str(STYLE))
    page.add_script_tag(path=str(SCRIPT))
    page.evaluate(
        """async state => {
            window.sent = [];
            const fetchImpl = async (url, options) => {
              if (options && options.body) window.sent.push(JSON.parse(options.body));
              return {ok: true, json: async () => state};
            };
            CellaPersonalBriefing.openJandiDialog({fetchImpl});
            await new Promise(resolve => setTimeout(resolve, 30));
        }""",
        state,
    )


def _state(**overrides):
    state = {
        "registered": True,
        "enabled": True,
        "masked": "…/abcd********",
        "send_at": "09:30",
        "send_time_choices": ["08:00", "09:30"],
        "sections": SECTIONS,
        "last_sent_at": "",
        "last_error": "",
    }
    state.update(overrides)
    return state


def test_sections_come_from_the_server_not_the_front(page):
    """⛔ 프론트가 목록을 만들면 항목이 하나 늘 때 화면에서 통째로 사라진다
    (`@@` 데이터소스 목록이 갈렸던 그 사고와 같은 부류)."""

    _open(page, _state())

    labels = page.locator(".briefing-jandi-group label span").all_inner_texts()
    assert labels == [row["label"] for row in SECTIONS]


def test_groups_are_shown_so_briefing_and_alerts_are_told_apart(page):
    _open(page, _state())

    groups = page.locator(".briefing-jandi-group-name").all_inner_texts()
    assert groups == ["브리핑", "알림"]


def test_the_stored_choice_is_preselected(page):
    """저장한 설정이 화면에 안 보이면 사용자는 매번 다시 고른다."""

    _open(page, _state())

    assert page.locator("input[data-section-key=meetings]").is_checked()
    assert not page.locator("input[data-section-key=mail]").is_checked(), \
        "껐던 항목이 켜진 채로 보인다"


def test_saving_sends_the_unchecked_keys(page):
    """⛔ **끈 것**을 보낸다. 켠 목록으로 주고받으면 나중에 항목이 늘었을 때
    이미 저장해 둔 사람에게 영영 안 보인다."""

    _open(page, _state())
    page.uncheck("input[data-section-key=report_share]")
    page.get_by_text("저장", exact=True).click()
    page.wait_for_timeout(50)

    sent = page.evaluate("() => window.sent")
    assert sent, "저장을 눌렀는데 아무것도 보내지 않았다"
    # 원래 꺼져 있던 mail + 방금 끈 report_share
    assert sorted(sent[-1]["muted_sections"]) == ["mail", "report_share"]


def test_turning_everything_back_on_sends_an_empty_list(page):
    """⚠️ 빈 목록은 '전부 받기' 다 — 키를 아예 빼면 서버는 '안 바꿈' 으로 읽는다.
    그러면 다시 켜는 것이 영영 저장되지 않는다."""

    _open(page, _state())
    page.check("input[data-section-key=mail]")
    page.get_by_text("저장", exact=True).click()
    page.wait_for_timeout(50)

    sent = page.evaluate("() => window.sent")
    assert sent[-1]["muted_sections"] == [], "전부 켰는데 빈 목록을 안 보냈다"


def test_the_dialog_still_works_when_the_server_sends_no_sections(page):
    """⚠️ 옛 서버 응답(항목 없음)에도 다이얼로그가 떠야 한다 — 배포 순서 때문에
    잠깐 그럴 수 있고, 그때 설정 화면이 통째로 안 뜨면 시각도 못 바꾼다."""

    _open(page, _state(sections=[]))

    assert page.locator(".briefing-jandi-select").count() == 1
    assert page.locator(".briefing-jandi-group").count() == 0
