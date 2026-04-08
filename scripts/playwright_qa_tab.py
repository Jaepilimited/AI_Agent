"""
Playwright focused test: Tab switch during streaming (AbortError issue)
Also tests: mid-stream conversation switch doesn't freeze UI
"""
import asyncio
import sys
import json
import urllib.request
from playwright.async_api import async_playwright

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

BASE_URL = "http://127.0.0.1:3002"
results = []


def log(name, status, detail=""):
    results.append({"test": name, "status": status, "detail": detail})
    icon = "PASS" if status == "pass" else "FAIL" if status == "fail" else "WARN"
    print(f"[{icon}] {name}: {detail}")


def get_jwt():
    data = json.dumps({
        "department": "Craver_Accounts > Users > Brand Division > Operations Dept > Data Business > 데이터분석",
        "name": "임재필",
        "password": "1234"
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{BASE_URL}/api/auth/signin",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    resp = urllib.request.urlopen(req)
    cookie = resp.headers.get("set-cookie", "")
    for part in cookie.split(";"):
        part = part.strip()
        if part.startswith("token="):
            return part[6:]
    return None


async def wait_response(page, max_sec=90):
    for i in range(max_sec // 2):
        typing = await page.locator(".typing-indicator").count()
        if typing == 0 and i > 2:
            return True
        await page.wait_for_timeout(2000)
    return False


async def main():
    print("=" * 60)
    print("Tab Switch / AbortError Test")
    print("=" * 60)

    token = get_jwt()
    if not token:
        print("[FATAL] Cannot get JWT")
        return

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False, slow_mo=100)
        context = await browser.new_context(
            viewport={"width": 1280, "height": 800},
            locale="ko-KR"
        )
        await context.add_cookies([{
            "name": "token", "value": token,
            "domain": "127.0.0.1", "path": "/",
            "httpOnly": True, "sameSite": "Lax"
        }])
        page = await context.new_page()

        await page.goto(f"{BASE_URL}/", wait_until="commit", timeout=60000)
        await page.wait_for_selector("#chat-input", state="visible", timeout=30000)
        print("[OK] Logged in")

        # ── Create Conversation 1 ──
        print("\n[STEP 1] Creating first conversation...")
        chat_input = page.locator("#chat-input")
        await chat_input.fill("첫번째 대화입니다. 안녕하세요.")
        await page.locator("#btn-send").click()
        await page.wait_for_timeout(3000)
        await wait_response(page)
        print("[OK] First conversation created")

        # ── Create Conversation 2 ──
        print("\n[STEP 2] Creating second conversation...")
        await page.locator("#btn-new-chat").click()
        await page.wait_for_timeout(1000)
        await chat_input.fill("두번째 대화입니다.")
        await page.locator("#btn-send").click()
        await page.wait_for_timeout(3000)
        await wait_response(page)
        print("[OK] Second conversation created")

        # Check sidebar has items
        await page.wait_for_timeout(1000)
        conv_items = page.locator(".convo-item")
        conv_count = await conv_items.count()
        print(f"[INFO] Sidebar conversation count: {conv_count}")

        if conv_count < 2:
            log("tab_switch_setup", "fail", f"Only {conv_count} conversations in sidebar")
            await browser.close()
            _print_summary()
            return

        log("tab_switch_setup", "pass", f"{conv_count} conversations ready")

        # ── Test A: Switch tab during streaming ──
        print("\n[STEP 3] Test: Switch conversation during streaming...")
        await page.locator("#btn-new-chat").click()
        await page.wait_for_timeout(500)

        # Send a long question that will stream for a while
        await chat_input.fill("분기별 매출 추이와 성장률을 상세하게 분석해주세요. 2024년과 2025년을 비교해서 테이블로 보여주세요.")
        await page.locator("#btn-send").click()
        print("[INFO] Message sent, waiting for streaming to start...")
        await page.wait_for_timeout(2500)

        # Check if streaming is active
        typing = await page.locator(".typing-indicator").count()
        print(f"[INFO] Typing indicator present: {typing > 0}")

        # NOW SWITCH to a different conversation
        print("[INFO] Switching to previous conversation...")
        await conv_items.first.click()
        await page.wait_for_timeout(3000)

        # ── Verify UI is not frozen ──
        # Test 1: Can we type in the input?
        input_visible = await chat_input.is_visible()
        input_enabled = await chat_input.is_enabled()
        print(f"[INFO] Input: visible={input_visible}, enabled={input_enabled}")

        can_type = False
        if input_visible and input_enabled:
            await chat_input.fill("tab switch test")
            val = await chat_input.input_value()
            can_type = (val == "tab switch test")
            await chat_input.fill("")

        if can_type:
            log("tab_switch_no_freeze", "pass", "UI responsive after mid-stream tab switch")
        else:
            log("tab_switch_no_freeze", "fail", f"UI frozen: visible={input_visible}, enabled={input_enabled}")

        # Test 2: Can we send a new message in the switched conversation?
        print("\n[STEP 4] Test: Send message after tab switch...")
        await chat_input.fill("탭 전환 후 새 메시지 테스트")
        await page.locator("#btn-send").click()
        await page.wait_for_timeout(3000)
        await wait_response(page)

        ai_msgs = page.locator(".message-assistant")
        ai_count = await ai_msgs.count()
        if ai_count > 0:
            last_content = await ai_msgs.last.locator(".message-content").text_content(timeout=5000)
            if last_content and len(last_content.strip()) > 3:
                log("tab_switch_new_msg", "pass", f"New message works after switch: {last_content[:60]}...")
            else:
                log("tab_switch_new_msg", "fail", "AI response empty after switch")
        else:
            log("tab_switch_new_msg", "fail", "No AI message after switch")

        # Test 3: Go back to the interrupted conversation
        print("\n[STEP 5] Test: Return to interrupted conversation...")
        conv_items2 = page.locator(".convo-item")
        count2 = await conv_items2.count()
        if count2 > 0:
            # Find the conversation with the long question
            for i in range(count2):
                text = await conv_items2.nth(i).text_content()
                if "분기별" in text or "매출" in text:
                    await conv_items2.nth(i).click()
                    await page.wait_for_timeout(2000)
                    break

            # Check messages are displayed
            msgs = page.locator(".message")
            msg_count = await msgs.count()
            print(f"[INFO] Messages in interrupted conv: {msg_count}")
            if msg_count > 0:
                log("tab_switch_return", "pass", f"Interrupted conversation has {msg_count} messages")
            else:
                log("tab_switch_return", "warn", "Interrupted conversation appears empty")

        # Test 4: Console errors check
        print("\n[STEP 6] Checking for console errors...")
        # Re-do a switch to capture any JS errors
        page.on("console", lambda msg: print(f"  [CONSOLE {msg.type}] {msg.text}") if msg.type == "error" else None)

        errors_found = []
        def capture_error(msg):
            if msg.type == "error":
                errors_found.append(msg.text)
        page.on("console", capture_error)

        # Do another quick switch
        if await conv_items.count() >= 2:
            await conv_items.nth(0).click()
            await page.wait_for_timeout(1000)
            await conv_items.nth(1).click()
            await page.wait_for_timeout(1000)

        if errors_found:
            log("console_errors", "warn", f"{len(errors_found)} errors: {'; '.join(errors_found[:3])}")
        else:
            log("console_errors", "pass", "No console errors during tab switching")

        await page.wait_for_timeout(2000)
        await browser.close()

    _print_summary()


def _print_summary():
    print("\n" + "=" * 60)
    print("TAB SWITCH TEST RESULTS")
    print("=" * 60)
    pass_c = sum(1 for r in results if r["status"] == "pass")
    fail_c = sum(1 for r in results if r["status"] == "fail")
    warn_c = sum(1 for r in results if r["status"] == "warn")
    for r in results:
        icon = "OK" if r["status"] == "pass" else "FAIL" if r["status"] == "fail" else "WARN"
        print(f"  [{icon}] {r['test']}: {r['detail'][:120]}")
    print(f"\nTotal: {pass_c} pass, {fail_c} fail, {warn_c} warn")

    with open("scripts/playwright_qa_tab_results.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    asyncio.run(main())
