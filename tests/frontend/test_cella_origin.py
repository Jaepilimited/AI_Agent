# -*- coding: utf-8 -*-
"""셀라의 기원 — 아바타를 눌러 영상이 열리는가, 그리고 **끼어들지 않는가**.

여기서 지키는 것은 두 방향이다:
  · 누르면 열린다 (기능)
  · 아무 때나 뜨지 않는다 (프롬프트 가이드를 가리지 않는다 · 거절하면 끝)

⛔ `cella-pet.js` 는 다른 세션이 작업 중이라 고치지 않았다. 그래서 이 모듈은
   그쪽 마크업에 **얹혀 있다** — 마크업이 바뀌면 조용히 사라진다. 그 전제를
   `test_the_host_markup_still_exists` 가 고정한다.
"""
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "app/frontend/cella-origin.js"
STYLE = ROOT / "app/static/style.css"
PET_STYLE = ROOT / "app/static/cella-pet.css"

# cella-pet.js 가 만드는 마크업의 최소 형태 (그쪽 파일을 그대로 실행하지 않는다 —
# 그 파일은 채팅 화면 전체를 전제로 한다)
HOST = """
<div class="chat-main" style="position:relative;height:700px">
  <div class="chat-welcome" id="chat-welcome">첫 화면</div>
  <div id="cella-pet-layer">
    <aside class="cella-guide">
      <div class="cella-guide-card"><strong>프롬프트 가이드</strong>
        <p class="cella-guide-text">기간·대상·형식을 함께 적어보세요.</p></div>
      <div class="cella-static-avatar" aria-hidden="true"><img alt=""></div>
    </aside>
  </div>
</div>
"""


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


@pytest.fixture
def page(browser):
    ctx = browser.new_context()
    pg = ctx.new_page()
    yield pg
    ctx.close()


def _open(page, seen=False, welcome=True):
    # ⚠️ `about:blank` 에서는 localStorage 가 막힌다 (불투명 출처) — 이 기능은
    #    "한 번만 묻는다" 를 거기에 기대므로 진짜 출처가 있어야 검사가 된다.
    #    서버는 띄우지 않고 요청만 가로채서 빈 문서를 준다
    page.route("**/*", lambda route: route.fulfill(
        status=200, content_type="text/html", body="<html><body></body></html>"))
    page.goto("http://cella.test/")
    page.set_content(HOST if welcome else HOST.replace(
        'id="chat-welcome">', 'id="chat-welcome" style="display:none">'))
    # ⛔ 진짜 CSS 를 둘 다 넣는다. `cella-pet.css` 의 `pointer-events: none`
    #    때문에 버튼이 안 눌리던 것을 이 테스트가 잡았다 — 한쪽만 넣으면
    #    통과해 버리고 프로덕션에서만 안 눌린다
    page.add_style_tag(path=str(PET_STYLE))
    page.add_style_tag(path=str(STYLE))
    if seen:
        page.evaluate("() => localStorage.setItem('skin1004-cella-origin-invited-v1','1')")
    # ⚠️ pause 를 감시해 둔다 — 닫을 때 정말 멈추는지 봐야 한다
    page.evaluate("""() => {
        window.__paused = 0;
        const orig = HTMLMediaElement.prototype.pause;
        HTMLMediaElement.prototype.pause = function () {
            window.__paused += 1;
            try { return orig.apply(this, arguments); } catch (e) { }
        };
        HTMLMediaElement.prototype.play = function () { return Promise.resolve(); };
    }""")
    page.add_script_tag(path=str(SCRIPT))
    return page


# ── 기능 ────────────────────────────────────────────────────────────────

def test_clicking_the_avatar_opens_the_video(page):
    _open(page, seen=True)
    page.click(".cella-origin-hotspot")
    assert page.is_visible(".cella-origin-overlay")
    src = page.get_attribute(".cella-origin-video", "src")
    assert src == "/static/media/cella-origin-720p.mp4"


def test_the_video_is_not_downloaded_until_opened(page):
    """⚠️ 40MB 다. 열지도 않은 사람에게 받게 하지 않는다."""
    _open(page, seen=True)
    page.click(".cella-origin-hotspot")
    assert page.get_attribute(".cella-origin-video", "preload") == "none"
    assert page.get_attribute(".cella-origin-video", "poster") == "/static/media/cella-origin-poster.jpg"


def test_the_hotspot_is_a_real_button_with_a_label(page):
    """⛔ `.cella-static-avatar` 는 `aria-hidden` 이다 — 그걸 그대로 누르게 하면
    키보드·스크린리더에서 닿지 않는다."""
    _open(page, seen=True)
    assert page.get_attribute(".cella-origin-hotspot", "aria-label") == "셀라 소개 보기"
    assert page.eval_on_selector(".cella-origin-hotspot", "el => el.tagName") == "BUTTON"


# ── 닫기 ────────────────────────────────────────────────────────────────

def test_escape_closes_it_and_stops_the_sound(page):
    """⛔ 닫을 때 멈추지 않으면 **화면은 사라졌는데 소리가 계속 난다.**"""
    _open(page, seen=True)
    page.click(".cella-origin-hotspot")
    page.keyboard.press("Escape")
    assert page.is_hidden(".cella-origin-overlay")
    assert page.evaluate("() => window.__paused") >= 1


def test_clicking_the_backdrop_closes_it(page):
    _open(page, seen=True)
    page.click(".cella-origin-hotspot")
    page.mouse.click(5, 5)
    assert page.is_hidden(".cella-origin-overlay")


def test_clicking_inside_the_card_does_not_close_it(page):
    """반대 방향 — 영상을 조작하다 닫히면 못 쓴다."""
    _open(page, seen=True)
    page.click(".cella-origin-hotspot")
    page.click(".cella-origin-card strong")
    assert page.is_visible(".cella-origin-overlay")


# ── 끼어들지 않는다 ─────────────────────────────────────────────────────

def test_the_invitation_shows_once_on_a_first_visit(page):
    _open(page, seen=False)
    assert page.is_visible(".cella-origin-invite")
    assert "볼래" in page.inner_text(".cella-origin-invite")


def test_a_returning_visitor_is_not_asked_again(page):
    """⛔ 매번 물으면 그게 소음이고, 소음이 되면 프롬프트 가이드까지 안 읽힌다."""
    _open(page, seen=True)
    assert page.locator(".cella-origin-invite").count() == 0


def test_declining_counts_as_seen(page):
    """⛔ 거절한 사람에게 또 묻지 않는다."""
    _open(page, seen=False)
    page.click(".cella-origin-invite-close")
    assert page.locator(".cella-origin-invite").count() == 0
    assert page.evaluate(
        "() => localStorage.getItem('skin1004-cella-origin-invited-v1')") == "1"


def test_watching_it_also_counts_as_seen(page):
    _open(page, seen=False)
    page.click(".cella-origin-invite-open")
    assert page.is_visible(".cella-origin-overlay")
    assert page.locator(".cella-origin-invite").count() == 0


def test_it_never_interrupts_an_ongoing_chat(page):
    """⛔ 첫 화면에서만 초대한다. 대화 중에 끼어들면 그건 방해다."""
    _open(page, seen=False, welcome=False)
    assert page.locator(".cella-origin-invite").count() == 0
    # 아바타 입구는 그대로 살아 있다
    assert page.locator(".cella-origin-hotspot").count() == 1


def test_the_prompt_guide_is_untouched(page):
    """⛔ 말풍선을 가로채지 않는다 — 그 자리는 질문 쓰는 법을 가르친다."""
    _open(page, seen=False)
    assert "기간·대상·형식" in page.inner_text(".cella-guide-text")


# ── 전제와 배선 ─────────────────────────────────────────────────────────

def test_the_host_markup_still_exists():
    """⚠️ 이 모듈은 `cella-pet.js` 의 마크업에 얹혀 있다. 그쪽이 이름을 바꾸면
    기능이 조용히 사라지므로, 전제를 여기서 고정한다."""
    pet = (ROOT / "app/frontend/cella-pet.js").read_text(encoding="utf-8")
    assert "cella-pet-layer" in pet
    assert "cella-static-avatar" in pet


def test_the_page_loads_the_module():
    html = (ROOT / "app/frontend/chat.html").read_text(encoding="utf-8")
    assert "cella-origin.js" in html


def test_the_video_is_not_shipped_with_every_deploy():
    """⛔ 40MB 가 배포 전송에 끼면 **매번** 함께 올라간다 (전체가 37.9MB 다)."""
    deploy = (ROOT / "scripts/deploy_new_server.py").read_text(encoding="utf-8")
    assert '".mp4"' in deploy
    assert (ROOT / "scripts/upload_media.py").exists(), "한 번만 올릴 방법이 있어야 한다"
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "app/static/media/*.mp4" in gitignore


def test_the_styles_use_real_theme_tokens():
    """⛔ 없는 CSS 변수는 에러가 아니라 폴백이라 테마 전환에서 조용히 빠진다."""
    import re
    css = STYLE.read_text(encoding="utf-8")
    block = css[css.index(".cella-origin-hotspot"):]
    for var in set(re.findall(r"var\((--[a-z-]+)", block)):
        assert re.search(re.escape(var) + r"\s*:", css), f"{var} 가 정의돼 있지 않다"
