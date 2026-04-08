"""Playwright test: @@multi-source query on real browser."""
import asyncio
import sys
import json
import time
import urllib.request
from playwright.async_api import async_playwright

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

BASE_URL = "http://127.0.0.1:3000"
USER_NAME = "임재필"
USER_DEPT = "Craver_Accounts > Users > Brand Division > Operations Dept > Data Business > 데이터분석"
PASSWORD = "1234"


def get_jwt_token():
    data = json.dumps({"department": USER_DEPT, "name": USER_NAME, "password": PASSWORD}).encode("utf-8")
    req = urllib.request.Request(f"{BASE_URL}/api/auth/signin", data=data, headers={"Content-Type": "application/json"}, method="POST")
    try:
        resp = urllib.request.urlopen(req)
        cookie_header = resp.headers.get("set-cookie", "")
        for part in cookie_header.split(";"):
            part = part.strip()
            if part.startswith("token="):
                return part[len("token="):]
    except Exception as e:
        print(f"[ERROR] Login failed: {e}")
    return None


async def main():
    token = get_jwt_token()
    if not token:
        print("[FAIL] Cannot get JWT token")
        return

    print(f"[OK] JWT token acquired")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport={"width": 1280, "height": 800})

        await context.add_cookies([{
            "name": "token", "value": token,
            "domain": "127.0.0.1", "path": "/",
            "httpOnly": True, "sameSite": "Lax"
        }])

        page = await context.new_page()
        await page.goto(BASE_URL, wait_until="commit", timeout=60000)
        await page.wait_for_timeout(3000)

        # Wait for chat UI
        try:
            await page.wait_for_selector("#chat-input", state="visible", timeout=30000)
            print("[OK] Chat UI loaded")
        except:
            print(f"[FAIL] Chat UI not loaded. URL: {page.url}")
            await browser.close()
            return

        # Send single-source query
        query = "@@인플루언서 이번달 유가 협업 gmwest1팀 알려줘"
        print(f"\n[TEST] Sending: {query}")
        t0 = time.time()

        chat_input = page.locator("#chat-input")
        await chat_input.fill(query)
        await page.wait_for_timeout(300)

        send_btn = page.locator("#btn-send")
        await send_btn.click()
        print(f"[INFO] Message sent at {time.strftime('%H:%M:%S')}")

        # Wait for response (up to 120s)
        await page.wait_for_timeout(5000)

        got_response = False
        for i in range(60):
            # Check typing indicator
            typing = await page.locator(".typing-indicator").count()

            # Check AI messages
            ai_msgs = page.locator(".message-assistant")
            ai_count = await ai_msgs.count()

            if ai_count > 0:
                content = ""
                try:
                    content = await ai_msgs.last.locator(".message-content").text_content(timeout=3000)
                except:
                    pass

                elapsed = time.time() - t0
                content_len = len(content) if content else 0

                if typing == 0 and i > 3 and content_len > 50:
                    print(f"\n[PASS] Response received in {elapsed:.1f}s ({content_len} chars)")
                    print(f"[PREVIEW] {content[:500]}")
                    got_response = True
                    break
                else:
                    print(f"  [{elapsed:.0f}s] streaming... typing={typing}, chars={content_len}", end="\r")

            await page.wait_for_timeout(2000)

        if not got_response:
            elapsed = time.time() - t0
            ai_count = await page.locator(".message-assistant").count()
            typing = await page.locator(".typing-indicator").count()
            print(f"\n[FAIL] No response after {elapsed:.1f}s (ai_msgs={ai_count}, typing={typing})")

            # Take screenshot for debug
            await page.screenshot(path="scripts/qa_screenshots/multi_test_fail.png")
            print("[INFO] Screenshot saved: scripts/qa_screenshots/multi_test_fail.png")

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
