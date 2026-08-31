"""잔디 설정 다이얼로그의 '받을 시각' — 실제 브라우저에서 고를 수 있는지.

여기서 잡으려는 것은 전부 **에러 없이 잘못 보이는** 종류다: 목록이 안 채워져도
콘솔은 조용하고, 저장 payload 에 시각이 빠져도 화면은 "저장됨" 이라고 말한다.
"""

from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "app/frontend/personal-briefing.js"
STYLE = ROOT / "app/static/style.css"

CHOICES = ["08:00", "08:30", "09:00", "09:30", "10:00"]


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
    """다이얼로그만 띄운다. 서버 응답은 state 로 흉내 낸다."""

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
        "send_time_choices": CHOICES,
        "last_sent_at": "",
        "last_error": "",
    }
    state.update(overrides)
    return state


def test_the_choices_come_from_the_server_response(page):
    """⛔ 프론트가 목록을 만들면 릴레이 회차가 바뀔 때 조용히 갈린다."""

    _open(page, _state())

    options = page.locator(".briefing-jandi-select option").all_inner_texts()
    assert options == CHOICES


def test_the_stored_time_is_preselected(page):
    """고른 값이 안 보이면 사용자는 매번 다시 고르고, 그때마다 저장을 눌러야 한다."""

    _open(page, _state())

    assert page.locator(".briefing-jandi-select").input_value() == "09:30"


def test_saving_sends_the_chosen_time(page):
    _open(page, _state())
    page.select_option(".briefing-jandi-select", "10:00")
    page.get_by_text("저장", exact=True).click()
    page.wait_for_timeout(50)

    assert page.evaluate("window.sent")[-1]["send_at"] == "10:00"


def test_the_time_can_be_changed_without_retyping_the_secret(page):
    """⛔ 주소는 가려서 내려오므로 사용자는 되붙일 수 없다 — 빈 값으로 보내야 한다."""

    _open(page, _state())
    page.select_option(".briefing-jandi-select", "08:30")
    page.get_by_text("저장", exact=True).click()
    page.wait_for_timeout(50)

    assert page.evaluate("window.sent")[-1]["webhook_url"] == ""


def test_the_status_line_says_when_it_will_arrive(page):
    """등록만 확인시키면 '언제 오는지' 를 화면 어디서도 알 수 없다."""

    _open(page, _state())

    assert "09:30" in page.locator(".briefing-jandi-status").inner_text()


def test_a_first_time_user_can_still_pick_a_time(page):
    """아직 등록 전이어도 시각은 고를 수 있어야 한다 — 등록과 같은 순간에 정한다."""

    _open(page, _state(registered=False, masked="", send_at="08:00"))

    select = page.locator(".briefing-jandi-select")
    assert select.is_enabled()
    assert select.input_value() == "08:00"


def test_without_a_choice_list_the_picker_stays_locked(page):
    """⛔ 목록을 못 받았으면 고르게 두지 않는다 — 지어낸 시각은 영영 오지 않는다."""

    _open(page, _state(send_time_choices=[]))

    assert page.locator(".briefing-jandi-select").is_disabled()


def test_the_picker_is_readable_in_light_mode(page):
    """⛔ 정의되지 않은 CSS 변수는 에러가 아니라 폴백이다 — 라이트 모드에서
    어두운 배경에 어두운 글자가 됐던 피드백 모달과 같은 사고를 막는다."""

    page.emulate_media(color_scheme="light")
    _open(page, _state())
    page.evaluate("document.documentElement.className = 'light'")

    colors = page.evaluate(
        """() => {
            const el = document.querySelector('.briefing-jandi-select');
            const style = getComputedStyle(el);
            return [style.backgroundColor, style.color];
        }"""
    )
    assert colors[0] != colors[1]
    assert "rgba(0, 0, 0, 0)" not in colors[0]
