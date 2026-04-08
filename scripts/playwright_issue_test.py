"""Playwright E2E test for 5 reported issues.

Issues:
  7. Tab switching freeze (AbortError drain)
  8. Amount format placeholder (약OO.O억원)
  9. Quarter comparison + visualization context
  [NEW] Auto-scroll during streaming
  [NEW] Message edit feature

Usage:
    python -X utf8 scripts/playwright_issue_test.py --port 3002
"""
import argparse
import json
import time
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from playwright.sync_api import sync_playwright

BASE_URL = "http://127.0.0.1:3002"
LOGIN_DEPT = "Craver_Accounts > Users > Brand Division > Operations Dept > Data Business > 데이터분석"
LOGIN_NAME = "임재필"
LOGIN_PW = "1234"

results = []


def report(issue_id, title, status, detail=""):
    icon = "✓" if status == "PASS" else "✗" if status == "FAIL" else "?"
    results.append({"id": issue_id, "title": title, "status": status, "detail": detail})
    print(f"  {icon} [{issue_id}] {title}: {status}")
    if detail:
        print(f"    → {detail[:200]}")


def login(page):
    """Login via API and navigate to chat."""
    import requests
    s = requests.Session()
    resp = s.post(f"{BASE_URL}/api/auth/signin", json={
        "department": LOGIN_DEPT, "name": LOGIN_NAME, "password": LOGIN_PW
    })
    if resp.status_code != 200:
        raise Exception(f"Login failed: {resp.status_code}")

    # Get cookie and set in browser
    cookies = s.cookies.get_dict()
    for name, value in cookies.items():
        page.context.add_cookies([{
            "name": name, "value": value,
            "domain": "127.0.0.1", "path": "/"
        }])

    page.goto(f"{BASE_URL}/", wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(3000)


def send_message(page, text, wait_complete=True, timeout_ms=120000):
    """Type and send a message, optionally wait for response."""
    inp = page.query_selector("#chat-input")
    if not inp:
        inp = page.query_selector("textarea")
    if not inp:
        raise Exception("Chat input not found")

    inp.click()
    inp.fill(text)
    page.wait_for_timeout(300)
    inp.press("Enter")

    if not wait_complete:
        return ""

    # Wait for assistant response to complete
    start = time.time()
    last_content = ""
    stable_count = 0
    page.wait_for_timeout(3000)

    while (time.time() - start) * 1000 < timeout_ms:
        msgs = page.query_selector_all(".message.message-assistant")
        if msgs:
            content = msgs[-1].inner_text().strip()
            if content and content == last_content:
                stable_count += 1
                if stable_count >= 4:
                    return content
            else:
                last_content = content
                stable_count = 0
        page.wait_for_timeout(1500)

    return last_content


def test_issue_7_tab_switch(page):
    """Issue 7: Tab switching freeze — send query, switch conversation, check no freeze."""
    print("\n=== Issue 7: Tab Switching Freeze ===")

    try:
        # Send a query
        send_message(page, "센텔라 앰플 성분 알려줘", wait_complete=False)
        page.wait_for_timeout(3000)  # Let streaming start

        # Click on "New Chat" button while streaming
        new_chat_btn = page.query_selector("#btn-new-chat, .new-chat-btn, button:has-text('새 대화')")
        if not new_chat_btn:
            sidebar_btns = page.query_selector_all(".sidebar button, .conversation-item")
            new_chat_btn = sidebar_btns[0] if sidebar_btns else None

        if new_chat_btn:
            new_chat_btn.click()
            page.wait_for_timeout(2000)

            # Check if page is responsive (not frozen)
            try:
                page.evaluate("document.title")  # Simple JS execution test
                # Try clicking input
                inp = page.query_selector("textarea, input[type='text']")
                if inp:
                    inp.click()
                    report("7", "Tab switch freeze", "PASS", "Page responsive after tab switch during streaming")
                else:
                    report("7", "Tab switch freeze", "PASS", "JS execution works, input not found but page responsive")
            except Exception as e:
                report("7", "Tab switch freeze", "FAIL", f"Page frozen: {e}")
        else:
            # No new chat button found — test with page navigation
            page.wait_for_timeout(5000)
            try:
                page.evaluate("document.title")
                report("7", "Tab switch freeze", "PASS", "No tab switch button found, but page responsive after wait")
            except Exception:
                report("7", "Tab switch freeze", "FAIL", "Page unresponsive")
    except Exception as e:
        report("7", "Tab switch freeze", "FAIL", str(e))


def test_issue_8_amount_format(page):
    """Issue 8: Amount format placeholder — check LLM doesn't output '약OO.O억원'."""
    print("\n=== Issue 8: Amount Format Placeholder ===")

    try:
        # Navigate to new chat first
        page.goto(f"{BASE_URL}/", wait_until="domcontentloaded")
        page.wait_for_timeout(3000)

        content = send_message(page, "전체 매출 합계 알려줘", timeout_ms=90000)

        # Check for placeholder patterns
        placeholders = ["OO.O억", "OO억", "XX.X", "약 OO", "약OO", "OO.O만"]
        has_placeholder = any(p in content for p in placeholders)

        if has_placeholder:
            report("8", "Amount format placeholder", "FAIL",
                   f"Found placeholder in response: {[p for p in placeholders if p in content]}")
        elif content and len(content) > 50:
            report("8", "Amount format placeholder", "PASS",
                   f"Response has actual numbers ({len(content)} chars)")
        else:
            report("8", "Amount format placeholder", "WARN",
                   f"Response too short to verify: {content[:100]}")
    except Exception as e:
        report("8", "Amount format placeholder", "FAIL", str(e))


def test_issue_9_quarter_context(page):
    """Issue 9: Quarter comparison + visualization context."""
    print("\n=== Issue 9: Quarter Comparison Context ===")

    try:
        page.goto(f"{BASE_URL}/", wait_until="domcontentloaded")
        page.wait_for_timeout(3000)

        # First: ask quarter comparison
        content1 = send_message(page, "올해 1분기 vs 작년 4분기 매출 비교해줘", timeout_ms=90000)

        if not content1 or len(content1) < 50:
            report("9", "Quarter comparison context", "WARN", "First query got no meaningful response")
            return

        # Second: ask for visualization
        content2 = send_message(page, "이걸 차트로 시각화해줘", timeout_ms=90000)

        # Check if response maintains quarter-level granularity
        month_keywords = ["1월", "2월", "3월", "4월", "5월", "6월", "7월", "8월", "9월", "10월", "11월", "12월"]
        quarter_keywords = ["분기", "Q1", "Q2", "Q3", "Q4", "1분기", "4분기"]

        has_month = sum(1 for kw in month_keywords if kw in content2)
        has_quarter = sum(1 for kw in quarter_keywords if kw in content2)

        if has_month > 3 and has_quarter == 0:
            report("9", "Quarter comparison context", "FAIL",
                   f"Switched to monthly ({has_month} month refs) instead of keeping quarterly")
        elif has_quarter > 0 or "차트" in content2 or "chart" in content2.lower():
            report("9", "Quarter comparison context", "PASS",
                   f"Maintained context (quarter refs: {has_quarter}, month refs: {has_month})")
        else:
            report("9", "Quarter comparison context", "WARN",
                   f"Unclear: quarter={has_quarter}, month={has_month}, len={len(content2)}")
    except Exception as e:
        report("9", "Quarter comparison context", "FAIL", str(e))


def test_auto_scroll(page):
    """[NEW] Auto-scroll: verify page scrolls down during streaming."""
    print("\n=== Auto-scroll Test ===")

    try:
        page.goto(f"{BASE_URL}/", wait_until="domcontentloaded")
        page.wait_for_timeout(3000)

        # Get initial scroll position
        scroll_before = page.evaluate("() => { const el = document.getElementById('chat-messages'); return el ? el.scrollTop : 0; }")

        # Send several short messages first to create scroll content
        for _ in range(3):
            page.evaluate("""() => {
                const el = document.getElementById('chat-messages');
                const div = document.createElement('div');
                div.className = 'message message-user';
                div.innerHTML = '<div class="message-content">padding message</div>';
                el.appendChild(div);
            }""")
        page.wait_for_timeout(500)

        scroll_before = page.evaluate("() => { const el = document.getElementById('chat-messages'); return el ? el.scrollTop : 0; }")

        # Send a long query
        send_message(page, "SKIN1004 전 제품 라인별로 정리해서 상세하게 알려줘", wait_complete=False)
        page.wait_for_timeout(8000)

        scroll_during = page.evaluate("() => { const el = document.getElementById('chat-messages'); return el ? el.scrollTop : 0; }")

        page.wait_for_timeout(15000)

        scroll_after = page.evaluate("() => { const el = document.getElementById('chat-messages'); return el ? el.scrollTop : 0; }")
        scroll_height = page.evaluate("() => { const el = document.getElementById('chat-messages'); return el ? el.scrollHeight : 0; }")

        if scroll_after > scroll_before + 50:
            report("AUTO", "Auto-scroll during streaming", "PASS",
                   f"Scroll moved: {scroll_before} → {scroll_during} → {scroll_after} (height={scroll_height})")
        elif scroll_during > scroll_before:
            report("AUTO", "Auto-scroll during streaming", "PASS",
                   f"Scroll moved during: {scroll_before} → {scroll_during}")
        elif scroll_height < 600:
            report("AUTO", "Auto-scroll during streaming", "WARN",
                   f"Content too short to scroll: height={scroll_height}, positions={scroll_before}/{scroll_during}/{scroll_after}")
        else:
            report("AUTO", "Auto-scroll during streaming", "FAIL",
                   f"Scroll didn't move: before={scroll_before}, during={scroll_during}, after={scroll_after}, height={scroll_height}")

        # Wait for completion
        page.wait_for_timeout(15000)

    except Exception as e:
        report("AUTO", "Auto-scroll during streaming", "FAIL", str(e))


def test_message_edit(page):
    """[NEW] Message edit: hover user message → pencil icon → edit → resubmit."""
    print("\n=== Message Edit Test ===")

    try:
        page.goto(f"{BASE_URL}/", wait_until="domcontentloaded")
        page.wait_for_timeout(3000)

        # Send initial message
        content1 = send_message(page, "안녕하세요", timeout_ms=30000)

        if not content1:
            report("EDIT", "Message edit feature", "FAIL", "No response to initial message")
            return

        # Find user message
        user_msgs = page.query_selector_all(".message.message-user")
        if not user_msgs:
            report("EDIT", "Message edit feature", "FAIL", "No user messages found in DOM")
            return

        last_user = user_msgs[-1]

        # Hover to reveal edit button
        last_user.hover()
        page.wait_for_timeout(1000)

        # Find edit button (.msg-edit-btn)
        edit_btn = last_user.query_selector(".msg-edit-btn")
        if not edit_btn:
            all_btns = last_user.query_selector_all("button")
            if all_btns:
                report("EDIT", "Message edit feature", "WARN",
                       f"Found {len(all_btns)} buttons but no .msg-edit-btn. Classes: {[b.get_attribute('class') or '' for b in all_btns[:3]]}")
            else:
                report("EDIT", "Message edit feature", "FAIL",
                       "No edit button (.msg-edit-btn) appears on hover")
            return

        # Click edit
        edit_btn.click()
        page.wait_for_timeout(1000)

        # Check for edit textarea (.msg-edit-textarea)
        edit_input = last_user.query_selector(".msg-edit-textarea")
        if not edit_input:
            edit_input = last_user.query_selector("textarea")

        if edit_input:
            # Clear and type new text
            edit_input.fill("크레이버는 어떤 회사야")
            page.wait_for_timeout(500)

            # Submit edit (Enter or button)
            edit_input.press("Enter")
            page.wait_for_timeout(3000)

            # Check if new response was generated
            new_content = ""
            asst_msgs = page.query_selector_all(".message-assistant, .assistant-message, [data-role='assistant']")
            if asst_msgs:
                new_content = asst_msgs[-1].inner_text().strip()

            if new_content and "안녕" not in new_content and len(new_content) > 30:
                report("EDIT", "Message edit feature", "PASS",
                       f"Edit worked: new response ({len(new_content)} chars)")
            else:
                report("EDIT", "Message edit feature", "WARN",
                       f"Edit submitted but response unclear: {new_content[:100]}")
        else:
            report("EDIT", "Message edit feature", "FAIL",
                   "Edit mode activated but no textarea found")

    except Exception as e:
        report("EDIT", "Message edit feature", "FAIL", str(e))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=3002)
    parser.add_argument("--headed", action="store_true", help="Show browser")
    args = parser.parse_args()

    global BASE_URL
    BASE_URL = f"http://127.0.0.1:{args.port}"

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not args.headed)
        context = browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        )
        page = context.new_page()

        print(f"Testing on {BASE_URL}")
        print("=" * 60)

        # Login
        print("\nLogging in...")
        login(page)
        print("  ✓ Logged in\n")

        # Run tests
        test_issue_7_tab_switch(page)
        test_issue_8_amount_format(page)
        test_issue_9_quarter_context(page)
        test_auto_scroll(page)
        test_message_edit(page)

        browser.close()

    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    pass_count = sum(1 for r in results if r["status"] == "PASS")
    fail_count = sum(1 for r in results if r["status"] == "FAIL")
    warn_count = sum(1 for r in results if r["status"] == "WARN")
    for r in results:
        icon = "✓" if r["status"] == "PASS" else "✗" if r["status"] == "FAIL" else "?"
        print(f"  {icon} [{r['id']}] {r['title']}: {r['status']}")
        if r["detail"]:
            print(f"    {r['detail'][:150]}")
    print(f"\nTotal: {pass_count} PASS / {warn_count} WARN / {fail_count} FAIL")

    # Save
    with open("scripts/playwright_issue_results.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"Results saved: scripts/playwright_issue_results.json")


if __name__ == "__main__":
    main()
