"""
Playwright QA Test: SKIN1004 AI Chat
Tests: login, message send, tab switch, auto-scroll, message edit
"""
import asyncio
import sys
import json
import urllib.request
import urllib.error
from playwright.async_api import async_playwright

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

BASE_URL = "http://127.0.0.1:3002"
USER_NAME = "임재필"
USER_DEPT = "Craver_Accounts > Users > Brand Division > Operations Dept > Data Business > 데이터분석"
PASSWORD = "1234"
results = []


def log(test_name, status, detail=""):
    results.append({"test": test_name, "status": status, "detail": detail})
    icon = "PASS" if status == "pass" else "FAIL" if status == "fail" else "WARN"
    print(f"[{icon}] {test_name}: {detail}")


def get_jwt_token():
    """Get JWT token via API call"""
    data = json.dumps({
        "department": USER_DEPT,
        "name": USER_NAME,
        "password": PASSWORD
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{BASE_URL}/api/auth/signin",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    try:
        resp = urllib.request.urlopen(req)
        cookie_header = resp.headers.get("set-cookie", "")
        # Extract token value
        for part in cookie_header.split(";"):
            part = part.strip()
            if part.startswith("token="):
                return part[len("token="):]
        return None
    except urllib.error.HTTPError as e:
        print(f"[ERROR] Login API failed: {e.code} {e.read().decode()}")
        return None


async def login_via_cookie(context, page):
    """Login by setting JWT cookie directly"""
    print("[INFO] Getting JWT token via API...")
    token = get_jwt_token()
    if not token:
        log("login", "fail", "Could not get JWT token from API")
        return False

    print(f"[INFO] Got token: {token[:30]}...")

    # Set cookie in browser context
    await context.add_cookies([{
        "name": "token",
        "value": token,
        "domain": "127.0.0.1",
        "path": "/",
        "httpOnly": True,
        "sameSite": "Lax"
    }])

    # Navigate to chat
    print("[INFO] Navigating to /chat...")
    await page.goto(f"{BASE_URL}/", wait_until="commit", timeout=60000)
    await page.wait_for_timeout(3000)

    # Wait for chat UI
    try:
        await page.wait_for_selector("#chat-input", state="visible", timeout=30000)
        print(f"[INFO] Chat page loaded: {page.url}")
        log("login", "pass", "JWT cookie auth successful")
        return True
    except:
        print(f"[INFO] Current URL after wait: {page.url}")
        # Debug page content
        try:
            body = await page.locator("body").inner_text(timeout=3000)
            print(f"[INFO] Page body (first 300): {body[:300]}")
        except:
            pass
        # Check for any input/textarea
        inputs = await page.locator("input, textarea").count()
        print(f"[INFO] Input/textarea count: {inputs}")
        # Check if redirected to login
        if "/login" in page.url:
            log("login", "fail", "Redirected to login - token may be invalid")
        else:
            log("login", "fail", "Chat input not found after login")
        return False


async def wait_for_response_complete(page, timeout_sec=90):
    """Wait for AI response to finish streaming"""
    for i in range(timeout_sec // 2):
        typing = await page.locator(".typing-indicator").count()
        if typing == 0 and i > 2:
            return True
        await page.wait_for_timeout(2000)
    return False


async def test_send_message(page):
    """Send a simple message and check response"""
    try:
        chat_input = page.locator("#chat-input")
        await chat_input.wait_for(state="visible", timeout=5000)
        await chat_input.fill("test")
        await page.wait_for_timeout(300)

        send_btn = page.locator("#btn-send")
        await send_btn.click()
        print("[INFO] Message sent, waiting for response...")

        await page.wait_for_timeout(3000)
        await wait_for_response_complete(page)

        ai_msgs = page.locator(".message-assistant")
        count = await ai_msgs.count()
        print(f"[INFO] AI messages count: {count}")

        if count > 0:
            content = await ai_msgs.last.locator(".message-content").text_content(timeout=5000)
            if content and len(content.strip()) > 3:
                log("send_message", "pass", f"Response: {content[:80]}...")
                return True
        log("send_message", "fail", "No valid AI response")
        return False
    except Exception as e:
        log("send_message", "fail", str(e)[:200])
        return False


async def test_auto_scroll(page):
    """Test auto-scroll during streaming"""
    try:
        new_btn = page.locator("#btn-new-chat")
        await new_btn.click()
        await page.wait_for_timeout(1000)

        chat_input = page.locator("#chat-input")
        await chat_input.fill("SKIN1004 센텔라 토너의 전 성분과 효능을 상세하게 설명해주세요. 각 성분이 피부에 어떤 영향을 미치는지도 설명해주세요.")
        send_btn = page.locator("#btn-send")
        await send_btn.click()
        await page.wait_for_timeout(3000)

        scroll_checks = []
        for i in range(6):
            await page.wait_for_timeout(2000)
            at_bottom = await page.evaluate("""() => {
                const el = document.getElementById('chat-messages');
                if (!el) return false;
                return Math.abs(el.scrollHeight - el.scrollTop - el.clientHeight) < 80;
            }""")
            scroll_checks.append(at_bottom)
            print(f"[INFO] Scroll check {i+1}: at_bottom={at_bottom}")

        await wait_for_response_complete(page)

        ok = sum(scroll_checks)
        if ok >= 3:
            log("auto_scroll", "pass", f"{ok}/6 checks at bottom during streaming")
        else:
            log("auto_scroll", "fail", f"Only {ok}/6 checks at bottom")
        return ok >= 3
    except Exception as e:
        log("auto_scroll", "fail", str(e)[:200])
        return False


async def test_tab_switch(page):
    """Test switching conversations doesn't freeze"""
    try:
        new_btn = page.locator("#btn-new-chat")
        await new_btn.click()
        await page.wait_for_timeout(1000)

        chat_input = page.locator("#chat-input")
        await chat_input.fill("탭 전환 테스트 메시지")
        send_btn = page.locator("#btn-send")
        await send_btn.click()

        # Switch while streaming
        await page.wait_for_timeout(2000)

        conv_items = page.locator(".conv-item")
        conv_count = await conv_items.count()
        print(f"[INFO] Conversation items: {conv_count}")

        if conv_count >= 2:
            await conv_items.nth(1).click()
            await page.wait_for_timeout(3000)

            input2 = page.locator("#chat-input")
            visible = await input2.is_visible()
            enabled = await input2.is_enabled()

            if visible and enabled:
                await input2.fill("UI test after switch")
                val = await input2.input_value()
                await input2.fill("")
                if val == "UI test after switch":
                    log("tab_switch", "pass", "UI responsive after tab switch during streaming")
                    return True
                else:
                    log("tab_switch", "fail", "Input not accepting text")
                    return False
            else:
                log("tab_switch", "fail", f"Input: visible={visible}, enabled={enabled}")
                return False
        else:
            log("tab_switch", "warn", f"Only {conv_count} conv items")
            return True
    except Exception as e:
        log("tab_switch", "fail", str(e)[:200])
        return False


async def test_message_edit(page):
    """Test edit message feature"""
    try:
        new_btn = page.locator("#btn-new-chat")
        await new_btn.click()
        await page.wait_for_timeout(1000)

        # Send a message
        chat_input = page.locator("#chat-input")
        await chat_input.fill("메시지 수정 테스트 원본")
        send_btn = page.locator("#btn-send")
        await send_btn.click()
        print("[INFO] Sent message for edit test")

        await page.wait_for_timeout(3000)
        await wait_for_response_complete(page)
        await page.wait_for_timeout(1000)

        # Find user message
        user_msgs = page.locator(".message-user")
        user_count = await user_msgs.count()
        print(f"[INFO] User messages: {user_count}")

        if user_count == 0:
            log("message_edit", "fail", "No user messages found")
            return False

        user_msg = user_msgs.last

        # Step 1: Check edit button exists in DOM
        edit_btn = user_msg.locator(".msg-edit-btn")
        edit_count = await edit_btn.count()
        print(f"[INFO] Edit button count: {edit_count}")

        if edit_count == 0:
            log("message_edit", "fail", "Edit button not found in DOM")
            return False

        # Step 2: Hover to reveal
        await user_msg.hover()
        await page.wait_for_timeout(800)

        edit_visible = await edit_btn.is_visible()
        print(f"[INFO] Edit button visible after hover: {edit_visible}")

        if not edit_visible:
            css = await edit_btn.evaluate("""el => {
                const s = window.getComputedStyle(el);
                return {display: s.display, opacity: s.opacity, visibility: s.visibility, pointerEvents: s.pointerEvents};
            }""")
            print(f"[INFO] Edit btn CSS: {css}")
            log("message_edit_btn_hover", "fail", f"Not visible: {json.dumps(css)}")
            # Force click anyway
            await edit_btn.click(force=True)
        else:
            log("message_edit_btn_hover", "pass", "Edit button visible on hover")
            await edit_btn.click()

        await page.wait_for_timeout(500)

        # Step 3: Check textarea
        textarea = user_msg.locator(".msg-edit-textarea")
        ta_count = await textarea.count()
        print(f"[INFO] Textarea count in user_msg: {ta_count}")

        if ta_count == 0:
            # Search page-wide
            ta_page = page.locator(".msg-edit-textarea")
            ta_page_count = await ta_page.count()
            print(f"[INFO] Textarea count in page: {ta_page_count}")
            if ta_page_count == 0:
                log("message_edit_textarea", "fail", "Textarea not created after click")
                return False
            textarea = ta_page.first

        ta_visible = await textarea.is_visible()
        if not ta_visible:
            log("message_edit_textarea", "fail", "Textarea exists but not visible")
            return False

        log("message_edit_textarea", "pass", "Textarea appeared")

        # Step 4: Check original text
        ta_value = await textarea.input_value()
        print(f"[INFO] Textarea value: '{ta_value}'")
        if "원본" in ta_value or "수정 테스트" in ta_value:
            log("message_edit_content", "pass", "Original text preserved")
        else:
            log("message_edit_content", "warn", f"Text: '{ta_value[:60]}'")

        # Step 5: Edit and save
        await textarea.fill("메시지 수정 테스트 수정본")
        await page.wait_for_timeout(300)

        # Find save button
        save_btn = page.locator(".msg-edit-save")
        save_count = await save_btn.count()
        print(f"[INFO] Save button count: {save_count}")

        if save_count == 0:
            log("message_edit_save", "fail", "Save button not found")
            return False

        await save_btn.first.click()
        await page.wait_for_timeout(2000)

        # Step 6: Verify edit applied
        user_msg2 = page.locator(".message-user").last
        bubble = user_msg2.locator(".message-content")
        if await bubble.count() > 0:
            text = await bubble.text_content()
            print(f"[INFO] Edited bubble text: '{text[:60]}'")
            if "수정본" in text:
                log("message_edit_apply", "pass", "Edit applied, message updated")
            else:
                log("message_edit_apply", "fail", f"Text after edit: '{text[:60]}'")
        else:
            log("message_edit_apply", "warn", "Could not verify bubble text")

        # Step 7: Wait for new AI response
        await page.wait_for_timeout(3000)
        await wait_for_response_complete(page)

        ai_msgs = page.locator(".message-assistant")
        ai_count = await ai_msgs.count()
        print(f"[INFO] AI messages after edit: {ai_count}")

        if ai_count > 0:
            content = await ai_msgs.last.locator(".message-content").text_content(timeout=5000)
            if content and len(content.strip()) > 3:
                log("message_edit_response", "pass", "New AI response received after edit")
                return True
        log("message_edit_response", "fail", "No AI response after edit")
        return False

    except Exception as e:
        log("message_edit", "fail", str(e)[:200])
        return False


async def test_escape_cancel(page):
    """Test: Escape cancels edit mode"""
    try:
        # Ensure we have a user message
        user_msgs = page.locator(".message-user")
        if await user_msgs.count() == 0:
            log("escape_cancel", "warn", "No user message")
            return True

        user_msg = user_msgs.last
        await user_msg.hover()
        await page.wait_for_timeout(500)

        edit_btn = user_msg.locator(".msg-edit-btn")
        if await edit_btn.count() == 0:
            log("escape_cancel", "warn", "No edit button")
            return True

        await edit_btn.click(force=True)
        await page.wait_for_timeout(500)

        textarea = page.locator(".msg-edit-textarea")
        if await textarea.count() == 0:
            log("escape_cancel", "fail", "Textarea not shown")
            return False

        await textarea.first.press("Escape")
        await page.wait_for_timeout(500)

        ta_gone = await textarea.count() == 0
        bubble = user_msg.locator(".message-content")
        bubble_vis = await bubble.is_visible() if await bubble.count() > 0 else False

        if ta_gone and bubble_vis:
            log("escape_cancel", "pass", "Escape restores original message")
            return True
        else:
            log("escape_cancel", "fail", f"textarea_gone={ta_gone}, bubble_vis={bubble_vis}")
            return False
    except Exception as e:
        log("escape_cancel", "fail", str(e)[:200])
        return False


async def main():
    print("=" * 60)
    print("SKIN1004 AI Chat - Playwright QA Test")
    print(f"Target: {BASE_URL}")
    print("=" * 60)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False, slow_mo=100)
        context = await browser.new_context(
            viewport={"width": 1280, "height": 800},
            locale="ko-KR"
        )
        page = await context.new_page()

        # Login via JWT cookie
        if not await login_via_cookie(context, page):
            print("\n[FATAL] Login failed, aborting tests")
            await browser.close()
            _print_summary()
            return

        # Test 1: Send message
        await test_send_message(page)

        # Test 2: Auto-scroll
        await test_auto_scroll(page)

        # Test 3: Tab switch
        await test_tab_switch(page)

        # Test 4: Message edit
        await test_message_edit(page)

        # Test 5: Escape cancel
        await test_escape_cancel(page)

        await page.wait_for_timeout(2000)
        await browser.close()

    _print_summary()


def _print_summary():
    print("\n" + "=" * 60)
    print("RESULTS SUMMARY")
    print("=" * 60)
    pass_c = sum(1 for r in results if r["status"] == "pass")
    fail_c = sum(1 for r in results if r["status"] == "fail")
    warn_c = sum(1 for r in results if r["status"] == "warn")
    for r in results:
        icon = "OK" if r["status"] == "pass" else "FAIL" if r["status"] == "fail" else "WARN"
        print(f"  [{icon}] {r['test']}: {r['detail'][:120]}")
    print(f"\nTotal: {pass_c} pass, {fail_c} fail, {warn_c} warn")

    with open("scripts/playwright_qa_results.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print("Results saved to scripts/playwright_qa_results.json")


if __name__ == "__main__":
    asyncio.run(main())
