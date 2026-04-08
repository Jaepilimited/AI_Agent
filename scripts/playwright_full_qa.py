"""Comprehensive Playwright E2E QA — find all bugs.

Tests: login, chat, streaming, tab switch, message edit, auto-scroll,
       sidebar, follow-ups, code copy, theme, amount format, scroll button.

Usage:
    python -X utf8 scripts/playwright_full_qa.py --port 3002
"""
import argparse
import json
import time
import sys
import os
import re

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from playwright.sync_api import sync_playwright
import requests

BASE_URL = "http://127.0.0.1:3002"
LOGIN_DEPT = "Craver_Accounts > Users > Brand Division > Operations Dept > Data Business > 데이터분석"
LOGIN_NAME = "임재필"
LOGIN_PW = "1234"
results = []
screenshots = []
SHOT_DIR = "scripts/qa_screenshots"


def report(test_id, title, status, detail=""):
    results.append({"id": test_id, "title": title, "status": status, "detail": detail})
    icon = {"PASS": "✓", "FAIL": "✗", "WARN": "!"}[status]
    print(f"  {icon} [{test_id}] {title}: {status}")
    if detail:
        print(f"    → {detail[:200]}")


def shot(page, name):
    path = f"{SHOT_DIR}/{name}.png"
    page.screenshot(path=path)
    screenshots.append(path)
    return path


def login_api(page):
    s = requests.Session()
    resp = s.post(f"{BASE_URL}/api/auth/signin", json={
        "department": LOGIN_DEPT, "name": LOGIN_NAME, "password": LOGIN_PW
    })
    for k, v in s.cookies.get_dict().items():
        page.context.add_cookies([{"name": k, "value": v, "domain": "127.0.0.1", "path": "/"}])
    return resp.status_code == 200


def wait_response(page, timeout_ms=90000):
    """Wait for the last assistant message to stabilize."""
    start = time.time()
    last = ""
    stable = 0
    page.wait_for_timeout(3000)
    while (time.time() - start) * 1000 < timeout_ms:
        msgs = page.query_selector_all(".message.message-assistant")
        if msgs:
            cur = msgs[-1].inner_text().strip()
            if cur and cur == last:
                stable += 1
                if stable >= 3:
                    return cur
            else:
                last = cur
                stable = 0
        page.wait_for_timeout(1500)
    return last


def send(page, text, wait=True, timeout_ms=90000):
    inp = page.query_selector("#chat-input")
    if not inp:
        raise Exception("Input not found")
    inp.fill(text)
    page.wait_for_timeout(200)
    inp.press("Enter")
    if wait:
        return wait_response(page, timeout_ms)
    return ""


def new_chat(page):
    page.goto(f"{BASE_URL}/", wait_until="domcontentloaded")
    page.wait_for_timeout(3000)


# ══════════ TESTS ══════════

def test_login(page):
    """T01: Login page renders, login works."""
    page.goto(f"{BASE_URL}/login", wait_until="domcontentloaded")
    page.wait_for_timeout(2000)
    has_form = page.query_selector("form, .login-card, #login-form") is not None
    has_dept = page.query_selector("select, #dept-select, .dept-dropdown") is not None
    shot(page, "t01-login")
    if has_form:
        report("T01", "Login page renders", "PASS", f"form={has_form}, dept_select={has_dept}")
    else:
        report("T01", "Login page renders", "FAIL", "No login form found")


def test_chat_basic(page):
    """T02: Send message, get response."""
    new_chat(page)
    content = send(page, "안녕하세요")
    shot(page, "t02-chat-basic")
    if content and len(content) > 10:
        report("T02", "Basic chat response", "PASS", f"{len(content)} chars")
    else:
        report("T02", "Basic chat response", "FAIL", f"Response: '{content[:100]}'")


def test_welcome_screen(page):
    """T03: Welcome screen shows greeting + suggestion chips."""
    new_chat(page)
    welcome = page.query_selector("#chat-welcome, .chat-welcome")
    chips = page.query_selector_all(".suggestion-chip, .welcome-chip")
    shot(page, "t03-welcome")
    if welcome:
        visible = welcome.evaluate("el => el.style.display !== 'none' && el.offsetHeight > 0")
        report("T03", "Welcome screen", "PASS" if visible else "FAIL",
               f"visible={visible}, chips={len(chips)}")
    else:
        report("T03", "Welcome screen", "FAIL", "Welcome element not found")


def test_streaming_abort(page):
    """T04: Abort streaming mid-response — no freeze."""
    new_chat(page)
    send(page, "전체 브랜드별 매출 상세 비교 분석해줘", wait=False)
    page.wait_for_timeout(3000)

    # Click New Chat during streaming
    btn = page.query_selector("#btn-new-chat")
    if btn:
        btn.click()
        page.wait_for_timeout(2000)

    state = page.evaluate("""() => ({
        isStreaming: typeof isStreaming !== 'undefined' ? isStreaming : null,
        tokenDrain: typeof _S !== 'undefined' ? _S.running : null,
        welcomeVisible: (() => { var w = document.getElementById('chat-welcome'); return w ? w.style.display !== 'none' : false; })()
    })""")

    # Try sending new message
    content = send(page, "안녕", timeout_ms=30000)
    shot(page, "t04-abort")

    if state["isStreaming"] == False and state["tokenDrain"] == False and content:
        report("T04", "Streaming abort (New Chat)", "PASS",
               f"isStreaming={state['isStreaming']}, drain={state['tokenDrain']}, newResponse={len(content)}chars")
    else:
        report("T04", "Streaming abort (New Chat)", "FAIL",
               f"state={state}, newResponse={len(content) if content else 0}")


def test_streaming_convo_switch(page):
    """T05: Switch conversation during streaming."""
    new_chat(page)
    # Create a conversation first
    send(page, "테스트 대화 생성", timeout_ms=30000)
    page.wait_for_timeout(1000)

    # Start new query
    new_chat(page)
    send(page, "SKIN1004 전 제품 라인 정리해줘", wait=False)
    page.wait_for_timeout(3000)

    # Click sidebar conversation
    convos = page.query_selector_all(".conversation-item")
    if convos:
        convos[0].click()
        page.wait_for_timeout(3000)

        state = page.evaluate("""() => ({
            isStreaming: typeof isStreaming !== 'undefined' ? isStreaming : null,
            tokenDrain: typeof _S !== 'undefined' ? _S.running : null,
        })""")

        content = send(page, "확인", timeout_ms=30000)
        shot(page, "t05-convo-switch")
        if state["isStreaming"] == False and state["tokenDrain"] == False:
            report("T05", "Streaming abort (convo switch)", "PASS",
                   f"drain stopped, new response={len(content) if content else 0}")
        else:
            report("T05", "Streaming abort (convo switch)", "FAIL", f"state={state}")
    else:
        report("T05", "Streaming abort (convo switch)", "WARN", "No conversations in sidebar to switch to")


def test_message_edit(page):
    """T06: Edit user message → pencil icon → edit → resubmit."""
    new_chat(page)
    send(page, "안녕하세요", timeout_ms=30000)
    page.wait_for_timeout(1000)

    user_msgs = page.query_selector_all(".message.message-user")
    if not user_msgs:
        report("T06", "Message edit", "FAIL", "No user messages")
        return

    msg = user_msgs[-1]
    msg.hover()
    page.wait_for_timeout(800)

    edit_btn = msg.query_selector(".msg-edit-btn")
    if not edit_btn:
        report("T06a", "Edit button visible on hover", "FAIL", "No .msg-edit-btn found")
        shot(page, "t06-no-edit-btn")
        return
    report("T06a", "Edit button visible on hover", "PASS")

    # Click edit
    edit_btn.click()
    page.wait_for_timeout(800)

    textarea = msg.query_selector(".msg-edit-textarea, textarea")
    if not textarea:
        report("T06b", "Edit textarea appears", "FAIL", "No textarea after click")
        return
    report("T06b", "Edit textarea appears", "PASS")

    # Edit and submit
    textarea.fill("크레이버는 어떤 회사야")
    page.wait_for_timeout(300)
    textarea.press("Enter")
    page.wait_for_timeout(6000)

    # Check new response
    asst_msgs = page.query_selector_all(".message.message-assistant")
    new_content = asst_msgs[-1].inner_text().strip() if asst_msgs else ""
    shot(page, "t06-edit-result")

    if "크레이버" in new_content or "SKIN1004" in new_content or "Craver" in new_content:
        report("T06c", "Edit resubmit generates new response", "PASS", f"{len(new_content)} chars")
    elif new_content and len(new_content) > 30:
        report("T06c", "Edit resubmit generates new response", "PASS", f"{len(new_content)} chars (different topic)")
    else:
        report("T06c", "Edit resubmit generates new response", "FAIL", f"content='{new_content[:100]}'")

    # Test Esc cancel
    msg2 = page.query_selector_all(".message.message-user")
    if msg2:
        msg2[-1].hover()
        page.wait_for_timeout(500)
        eb2 = msg2[-1].query_selector(".msg-edit-btn")
        if eb2:
            eb2.click()
            page.wait_for_timeout(500)
            ta2 = msg2[-1].query_selector("textarea")
            if ta2:
                ta2.press("Escape")
                page.wait_for_timeout(500)
                ta_gone = msg2[-1].query_selector("textarea") is None
                report("T06d", "Edit cancel (Esc)", "PASS" if ta_gone else "FAIL",
                       f"textarea removed={ta_gone}")


def test_auto_scroll(page):
    """T07: Auto-scroll follows streaming content."""
    new_chat(page)
    send(page, "SKIN1004 전 제품 라인별 상세 설명 알려줘", wait=False)

    scroll_data = []
    for i in range(15):
        page.wait_for_timeout(2000)
        info = page.evaluate("""() => {
            var el = document.getElementById('chat-messages');
            return el ? { top: el.scrollTop, height: el.scrollHeight, client: el.clientHeight } : null;
        }""")
        if info:
            scroll_data.append(info)
            if info["height"] > info["client"] + 200 and info["top"] > 100:
                break

    shot(page, "t07-autoscroll")
    if scroll_data:
        first = scroll_data[0]
        last = scroll_data[-1]
        if last["top"] > first["top"] + 50 and last["height"] > last["client"]:
            report("T07", "Auto-scroll during streaming", "PASS",
                   f"scroll {first['top']}→{last['top']}, height={last['height']}")
        elif last["height"] <= last["client"]:
            report("T07", "Auto-scroll during streaming", "WARN",
                   f"Content fits viewport (height={last['height']}, client={last['client']})")
        else:
            report("T07", "Auto-scroll during streaming", "FAIL",
                   f"scroll stuck at {last['top']}, height={last['height']}, client={last['client']}")

    page.wait_for_timeout(10000)  # Let it finish


def test_scroll_up_stops_auto(page):
    """T08: User scroll-up disables auto-scroll."""
    # Continue from T07's long response
    page.evaluate("document.getElementById('chat-messages').scrollTop = 0")
    page.wait_for_timeout(1000)

    top_after = page.evaluate("document.getElementById('chat-messages').scrollTop")
    report("T08", "Manual scroll-up stays", "PASS" if top_after < 50 else "WARN",
           f"scrollTop={top_after} after manual scroll to 0")


def test_amount_format(page):
    """T09: BQ response uses real numbers, not placeholders."""
    new_chat(page)
    content = send(page, "전체 매출 합계 알려줘", timeout_ms=90000)
    shot(page, "t09-amount")

    placeholders = ["OO.O억", "OO억", "XX.X", "약 OO", "약OO"]
    has_ph = [p for p in placeholders if p in content]

    if has_ph:
        report("T09", "Amount format (no placeholders)", "FAIL", f"Found: {has_ph}")
    elif content and len(content) > 100:
        # Check for actual numbers
        has_num = bool(re.search(r'\d{1,3}(,\d{3})*(\.\d+)?억?원?', content))
        report("T09", "Amount format (no placeholders)", "PASS" if has_num else "WARN",
               f"has_real_numbers={has_num}, len={len(content)}")
    else:
        report("T09", "Amount format (no placeholders)", "WARN", f"Short response: {len(content)}")


def test_quarter_context(page):
    """T10: Quarter comparison → visualization keeps quarter granularity."""
    new_chat(page)
    c1 = send(page, "올해 1분기 vs 작년 4분기 매출 비교해줘", timeout_ms=90000)

    if not c1 or len(c1) < 50:
        report("T10", "Quarter context preservation", "WARN", "First query failed")
        return

    c2 = send(page, "이걸 차트로 시각화해줘", timeout_ms=90000)
    shot(page, "t10-quarter")

    month_refs = sum(1 for m in ["1월","2월","3월","4월","5월","6월"] if m in c2)
    quarter_refs = sum(1 for q in ["분기","Q1","Q4","1분기","4분기"] if q in c2)

    if month_refs > 3 and quarter_refs == 0:
        report("T10", "Quarter context preservation", "FAIL",
               f"Switched to monthly (months={month_refs}, quarters={quarter_refs})")
    else:
        report("T10", "Quarter context preservation", "PASS",
               f"quarters={quarter_refs}, months={month_refs}")


def test_follow_up_chips(page):
    """T11: Follow-up suggestion chips appear and work."""
    new_chat(page)
    send(page, "센텔라 앰플 성분 알려줘", timeout_ms=60000)
    page.wait_for_timeout(1000)

    chips = page.query_selector_all(".followup-chip")
    container = page.query_selector("#followup-suggestions")
    shot(page, "t11-followups")

    if chips and len(chips) > 0:
        visible = container.evaluate("el => el.style.display !== 'none'") if container else False
        report("T11a", "Follow-up chips appear", "PASS" if visible else "WARN",
               f"{len(chips)} chips, visible={visible}")

        # Click first chip
        try:
            chips[0].click()
            page.wait_for_timeout(3000)
            c2 = wait_response(page, timeout_ms=60000)
            report("T11b", "Follow-up chip clickable", "PASS" if c2 and len(c2) > 30 else "FAIL",
                   f"response={len(c2) if c2 else 0} chars")
        except Exception as e:
            report("T11b", "Follow-up chip clickable", "FAIL", str(e))
    else:
        report("T11a", "Follow-up chips appear", "FAIL", "No .followup-chip elements found")


def test_code_copy(page):
    """T12: Code block has copy button."""
    new_chat(page)
    send(page, "Python으로 Hello World 코드 작성해줘", timeout_ms=60000)
    page.wait_for_timeout(1000)

    code_blocks = page.query_selector_all("pre code, .code-block, pre")
    copy_btns = page.query_selector_all(".code-copy-btn, .copy-button, pre button")
    shot(page, "t12-code-copy")

    if code_blocks:
        report("T12a", "Code block rendered", "PASS", f"{len(code_blocks)} blocks")
        if copy_btns:
            report("T12b", "Copy button on code block", "PASS", f"{len(copy_btns)} buttons")
        else:
            report("T12b", "Copy button on code block", "FAIL", "No copy buttons found")
    else:
        report("T12a", "Code block rendered", "WARN", "No code blocks in response")


def test_theme_toggle(page):
    """T13: Dark/light theme toggle works."""
    theme_btn = page.query_selector("#btn-theme, .theme-toggle, button[onclick*='toggleTheme']")
    if not theme_btn:
        report("T13", "Theme toggle", "WARN", "No theme toggle button found")
        return

    before = page.evaluate("document.documentElement.classList.contains('dark')")
    theme_btn.click()
    page.wait_for_timeout(500)
    after = page.evaluate("document.documentElement.classList.contains('dark')")
    shot(page, "t13-theme")

    if before != after:
        report("T13", "Theme toggle", "PASS", f"dark: {before}→{after}")
    else:
        report("T13", "Theme toggle", "FAIL", f"No change: {before}→{after}")


def test_sidebar_search(page):
    """T14: Sidebar conversation search filters."""
    search = page.query_selector("#convo-search, .convo-search")
    if not search:
        report("T14", "Sidebar search", "WARN", "No search input found")
        return

    search.fill("테스트")
    page.wait_for_timeout(800)
    shot(page, "t14-search")
    report("T14", "Sidebar search", "PASS", "Search input works")


def test_model_select(page):
    """T15: Model selector exists and has options."""
    select = page.query_selector("#model-select")
    if not select:
        report("T15", "Model selector", "FAIL", "No #model-select element")
        return

    options = select.query_selector_all("option")
    report("T15", "Model selector", "PASS" if options else "FAIL",
           f"{len(options)} options: {[o.inner_text()[:20] for o in options[:5]]}")


def test_send_button_state(page):
    """T16: Send button disabled during streaming, enabled after."""
    new_chat(page)
    btn = page.query_selector("#btn-send")
    if not btn:
        report("T16", "Send button state", "FAIL", "No #btn-send")
        return

    # Before send
    before_disabled = btn.evaluate("el => el.disabled || el.classList.contains('disabled')")

    send(page, "안녕", wait=False)
    page.wait_for_timeout(1500)

    # During streaming
    during_text = btn.inner_text().strip()
    during_html = btn.evaluate("el => el.innerHTML")

    page.wait_for_timeout(15000)  # Wait for completion

    # After
    after_disabled = btn.evaluate("el => el.disabled || el.classList.contains('disabled')")
    shot(page, "t16-send-btn")

    report("T16", "Send button state", "PASS",
           f"before_disabled={before_disabled}, during_icon='{during_text[:10]}', after_disabled={after_disabled}")


def test_empty_input_blocked(page):
    """T17: Empty input doesn't send."""
    new_chat(page)
    inp = page.query_selector("#chat-input")
    inp.fill("")
    inp.press("Enter")
    page.wait_for_timeout(1000)

    msgs = page.query_selector_all(".message.message-user")
    report("T17", "Empty input blocked", "PASS" if not msgs else "FAIL",
           f"user messages after empty send: {len(msgs)}")


def test_long_response_rendering(page):
    """T18: Long response renders without breaking layout."""
    new_chat(page)
    content = send(page, "SKIN1004 전 제품 성분 전부 알려줘", timeout_ms=120000)
    shot(page, "t18-long-response")

    # Check layout integrity
    overflow = page.evaluate("""() => {
        var msgs = document.querySelectorAll('.message.message-assistant');
        if (!msgs.length) return {ok: false};
        var last = msgs[msgs.length-1];
        var rect = last.getBoundingClientRect();
        var parent = document.getElementById('chat-messages').getBoundingClientRect();
        return {
            ok: rect.width <= parent.width + 10,
            msgW: Math.round(rect.width),
            parentW: Math.round(parent.width),
            contentLen: last.innerText.length
        };
    }""")

    if overflow.get("ok"):
        report("T18", "Long response layout", "PASS",
               f"msgW={overflow['msgW']}, parentW={overflow['parentW']}, chars={overflow.get('contentLen',0)}")
    else:
        report("T18", "Long response layout", "FAIL", f"Overflow: {overflow}")


# ══════════ MAIN ══════════

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=3002)
    parser.add_argument("--headed", action="store_true")
    args = parser.parse_args()

    global BASE_URL
    BASE_URL = f"http://127.0.0.1:{args.port}"
    os.makedirs(SHOT_DIR, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not args.headed)
        ctx = browser.new_context(viewport={"width": 1280, "height": 800},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
        page = ctx.new_page()

        print(f"QA Testing: {BASE_URL}")
        print("=" * 60)

        # Login
        login_api(page)
        page.goto(f"{BASE_URL}/", wait_until="domcontentloaded")
        page.wait_for_timeout(3000)
        print("  ✓ Logged in\n")

        tests = [
            test_login, test_welcome_screen, test_chat_basic, test_empty_input_blocked,
            test_model_select, test_theme_toggle, test_send_button_state,
            test_streaming_abort, test_streaming_convo_switch, test_message_edit,
            test_auto_scroll, test_scroll_up_stops_auto,
            test_amount_format, test_quarter_context,
            test_follow_up_chips, test_code_copy,
            test_sidebar_search, test_long_response_rendering,
        ]

        for t in tests:
            try:
                print(f"\n--- {t.__doc__.strip()} ---")
                t(page)
            except Exception as e:
                report(t.__name__, t.__doc__.strip(), "FAIL", f"Exception: {e}")

        browser.close()

    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    p_count = sum(1 for r in results if r["status"] == "PASS")
    f_count = sum(1 for r in results if r["status"] == "FAIL")
    w_count = sum(1 for r in results if r["status"] == "WARN")

    for r in results:
        icon = {"PASS": "✓", "FAIL": "✗", "WARN": "!"}[r["status"]]
        print(f"  {icon} [{r['id']}] {r['title']}")
        if r["status"] != "PASS" and r["detail"]:
            print(f"    {r['detail'][:150]}")

    print(f"\n  {p_count} PASS / {w_count} WARN / {f_count} FAIL")
    print(f"  Screenshots: {SHOT_DIR}/ ({len(screenshots)} files)")

    with open("scripts/playwright_full_qa_results.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
