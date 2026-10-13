"""SKIN1004 AI Guide screenshots - correct login flow: name -> dept -> password"""
import asyncio, sys, os, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
from playwright.async_api import async_playwright

BASE = "http://127.0.0.1:3000"
OUT  = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "remotion-guide", "public", "screenshots")
os.makedirs(OUT, exist_ok=True)

async def shot(page, name):
    p = os.path.join(OUT, f"{name}.png")
    await page.screenshot(path=p, full_page=False)
    print(f"  saved {name}.png")

async def main():
    async with async_playwright() as pw:
        br = await pw.chromium.launch(headless=False)  # visible for debug
        ctx = await br.new_context(viewport={"width": 1440, "height": 900})
        page = await ctx.new_page()

        # 1. Login page (clean)
        print("[1] login page")
        await page.goto(BASE, wait_until="networkidle")
        await page.wait_for_timeout(1500)
        await shot(page, "01_login_page")

        # 2. Type name first (login flow: name -> dept -> pw)
        print("[2] typing name")
        name_inp = page.locator("#input-name")
        await name_inp.fill("")
        await name_inp.type("임재필", delay=80)
        await page.wait_for_timeout(1200)  # wait for dept dropdown to populate
        await shot(page, "02_name_typed")

        # 3. Dept dropdown (now populated)
        print("[3] dept dropdown")
        dept = page.locator("#input-dept")
        opts = await dept.locator("option").all()
        print(f"  dept options: {len(opts)}")
        for o in opts:
            val = await o.get_attribute("value")
            txt = await o.inner_text()
            if val and val != "":
                print(f"  selecting: {txt}")
                await dept.select_option(value=val)
                break
        await page.wait_for_timeout(500)
        await shot(page, "03_dept_selected")

        # 4. Password field filled (show placeholder dots)
        print("[4] password field")
        pw_inp = page.locator("#input-password")
        await pw_inp.click()
        await shot(page, "04_before_login")

        # 5. Actual login
        print("[5] login")
        await pw_inp.fill("jrj2002")
        await page.locator("#btn-submit").click()
        await page.wait_for_timeout(5000)
        url = page.url
        print(f"  after login url: {url}")
        await shot(page, "05_chat_main")

        # verify we're in chat
        chat_area = page.locator(".chat-area, #chat-area, .messages, #messages, .main-content")
        if await chat_area.count() == 0:
            print("  WARNING: might still be on login page!")

        # 6. Welcome screen
        print("[6] welcome screen")
        await page.wait_for_timeout(1000)
        await shot(page, "06_welcome")

        # 7. BQ question - type it
        print("[7] BQ question typing")
        textarea = page.locator("textarea").first
        await textarea.wait_for(state="visible", timeout=10000)
        await textarea.click()
        await textarea.fill("2025년 1분기 국가별 매출 현황을 알려줘")
        await shot(page, "07_bq_typed")

        # 8. Send and wait for response
        print("[8] BQ answer (waiting 50s)")
        await textarea.press("Enter")
        await page.wait_for_timeout(50000)
        await shot(page, "08_bq_answer")

        # 9. Scroll down to see chart
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await page.wait_for_timeout(2000)
        await shot(page, "09_chart_full")

        # 10. New chat - CS question
        print("[10] CS question")
        # Try keyboard shortcut or find new chat button
        new_btn = page.locator("button.new-chat, .new-chat-btn, [title='새 대화'], [aria-label*='새']").first
        found_new = await new_btn.count() > 0
        if found_new:
            await new_btn.click()
        else:
            # try clicking logo or refresh
            await page.keyboard.press("Control+Shift+Delete")

        await page.wait_for_timeout(1500)
        ta2 = page.locator("textarea").first
        await ta2.wait_for(state="visible", timeout=5000)
        await ta2.fill("마데카솔 크림은 어떤 피부 타입에 적합한가요?")
        await shot(page, "10_cs_typed")
        await ta2.press("Enter")
        await page.wait_for_timeout(20000)
        await shot(page, "11_cs_answer")

        # 11. Sidebar with conversations
        print("[11] sidebar")
        await shot(page, "12_sidebar_full")

        # 12. Follow-up chips (if visible)
        print("[12] follow-up chips")
        chips = page.locator(".followup-chip, .suggestion-chip, .chip")
        if await chips.count() > 0:
            await shot(page, "13_followup_chips")

        await br.close()
        imgs = sorted(f for f in os.listdir(OUT) if f.endswith(".png"))
        print(f"\nDone! {len(imgs)} screenshots saved to:\n{OUT}")
        for i in imgs:
            sz = os.path.getsize(os.path.join(OUT, i)) // 1024
            print(f"  {i} ({sz}KB)")

asyncio.run(main())
