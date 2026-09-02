"""첫 화면 추천 칩은 **두 줄 모두** 눌려야 한다.

2026-09-02 사용자 제보: *"체크해놓은 라인은 클릭시 아무런 반응이 없습니다.
그 아래있는 두번째 줄 라인은 버튼 클릭시 바로 답변이 나오구여"* — 위쪽
`내가 자주 묻는 것` 줄만 죽어 있었다.

원인은 **클릭 위임 대상이 좁았던 것**이다. 위임은 `#welcome-suggestions`(기본 칩 줄)에
걸려 있었는데, 개인 제안 줄은 `loadMySuggestions()` 가 그 **바깥에 형제로** 만든다
(기본 칩과 줄을 나누라는 2026-08-27 지적 때문이다). 그래서 위임이 닿지 않았다.

⛔ 에러가 나지 않는다. 콘솔도 조용하고 칩도 멀쩡히 보인다 — 눌러 보기 전까지 아무도
   모른다. `chat.js` 의 원래 주석이 *"나중에 추가한 칩은 눌러도 아무 일이 없다"* 라고
   경고하던 바로 그 실패가, 위임을 도입한 뒤에 **형태만 바꿔** 되살아났다.
"""

from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[2]
CHAT_HTML = ROOT / "app/frontend/chat.html"
CHAT_JS = ROOT / "app/frontend/chat.js"


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


#: 실제 배선만 떼어 온 하네스. `chat.js` 전체는 로그인·fetch 를 타서 브라우저에서
#: 그대로 못 돌린다. ⚠️ 그래서 **두 조각을 원본에서 문자열로 확인**한다 (아래 정적 검사).
_HARNESS = """
  window.__sent = [];
  var chatInput = document.getElementById("chat-input");
  function sendMessage() { window.__sent.push(chatInput.value); }

  // ── chat.js 의 위임 배선 (검증 대상) ─────────────────────────
  var suggestionsBox = document.getElementById("chat-welcome")
    || document.getElementById("welcome-suggestions");
  if (suggestionsBox) {
    suggestionsBox.addEventListener("click", function (event) {
      var chip = event.target.closest(".suggestion-chip");
      if (!chip || !chip.dataset.q) return;
      chatInput.value = chip.dataset.q;
      chatInput.dispatchEvent(new Event("input"));
      sendMessage();
    });
  }

  // ── loadMySuggestions() 의 DOM 삽입 (검증 대상) ──────────────
  var box = document.getElementById("welcome-suggestions");
  var frag = document.createDocumentFragment();
  [{text: "8월 전사 매출액 팀별로 알려줘", n: 3},
   {text: "이번달 누적 실적 알려줘", n: 1}].forEach(function (item) {
    var chip = document.createElement("button");
    chip.className = "suggestion-chip mine";
    chip.dataset.q = item.text;
    chip.textContent = item.text.length > 20
      ? item.text.slice(0, 20) + "\\u2026" : item.text;
    frag.appendChild(chip);
  });
  var mineRow = document.createElement("div");
  mineRow.id = "welcome-suggestions-mine";
  mineRow.className = "suggestions suggestions-mine";
  box.parentNode.insertBefore(mineRow, box);
  mineRow.replaceChildren(frag);
"""


def _prepared(page):
    page.set_content(CHAT_HTML.read_text(encoding="utf-8"))
    page.evaluate(_HARNESS)
    return page


def test_my_frequent_question_chips_actually_send(page):
    """⛔ 사용자가 제보한 그 줄 — 눌러도 아무 일이 없었다."""
    _prepared(page)

    mine = page.locator("#welcome-suggestions-mine .suggestion-chip")
    assert mine.count() == 2, "개인 제안 줄이 만들어지지 않았다"

    mine.first.click()

    assert page.evaluate("window.__sent") == ["8월 전사 매출액 팀별로 알려줘"], \
        "개인 제안 칩을 눌렀는데 질문이 전송되지 않았다"


def test_the_default_chip_row_still_sends(page):
    """⚠️ 위임 대상을 넓히면서 원래 되던 줄을 깨뜨리면 안 된다."""
    _prepared(page)

    page.locator("#welcome-suggestions .suggestion-chip").first.click()

    assert page.evaluate("window.__sent") == ["2026년 일본 매출 보고서 만들어줘"]


def test_both_rows_send_the_full_question_not_the_shortened_label(page):
    """⚠️ 칩 글자는 20자로 줄여 보여준다 — 보내는 것은 **원문**이어야 한다."""
    _prepared(page)

    chip = page.locator("#welcome-suggestions-mine .suggestion-chip").first
    shown = chip.inner_text()
    chip.click()

    sent = page.evaluate("window.__sent")[0]
    assert sent == "8월 전사 매출액 팀별로 알려줘"
    assert sent != shown or "…" not in shown


def test_clicking_the_welcome_background_sends_nothing(page):
    """⚠️ 위임을 조상으로 올렸으니 칩이 아닌 곳을 눌러도 조용해야 한다.

    `#chat-welcome` 안에는 인사말과 Today 브리핑이 함께 있다.
    """
    _prepared(page)

    page.locator(".welcome-sub").click()

    assert page.evaluate("window.__sent") == []


def test_the_delegation_target_contains_every_chip_row():
    """⛔ 정적 검사 — 위임 대상을 다시 좁히면 그 줄이 **에러 없이** 죽는다.

    개인 제안 줄은 `#welcome-suggestions` 바깥(형제)에 붙으므로, 위임은 반드시
    공통 조상인 `#chat-welcome` 에 걸려야 한다.
    """
    source = CHAT_JS.read_text(encoding="utf-8")

    assert 'var suggestionsBox = document.getElementById("chat-welcome")' in source, \
        "칩 클릭 위임이 공통 조상(#chat-welcome)에 걸려 있지 않다"
    assert "box.parentNode.insertBefore(mineRow, box)" in source, \
        "개인 제안 줄의 삽입 위치가 바뀌었다 — 위임 대상을 함께 확인할 것"

    html = CHAT_HTML.read_text(encoding="utf-8")
    welcome = html.index('id="chat-welcome"')
    assert welcome < html.index('id="welcome-suggestions"'), \
        "#welcome-suggestions 가 #chat-welcome 바깥으로 나갔다"
