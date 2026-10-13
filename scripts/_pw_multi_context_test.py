"""Playwright: 2턴 테스트 — BQ 질문 후 multi 라우트 참조형 후속 질문 (타임아웃+컨텍스트 검증)"""
import asyncio, sys, time
from playwright.async_api import async_playwright

URL = "http://127.0.0.1:3001"
Q1 = "2026년 1분기 미국 매출 알려줘"
Q2 = "방금 그 매출 수치가 시장 상황 대비 어떤지 외부 요인이랑 같이 분석해줘"

async def wait_answer(page, min_len=80, max_loops=150):
    prev, stable, text = "", 0, ""
    for _ in range(max_loops):
        await page.wait_for_timeout(2000)
        text = await page.evaluate("""() => {
            const msgs = document.querySelectorAll('.message.assistant, .msg.assistant, [class*="assistant"]');
            if (!msgs.length) return '';
            return msgs[msgs.length - 1].innerText || '';
        }""")
        if text and len(text) > min_len and text == prev:
            stable += 1
            if stable >= 3:
                break
        else:
            stable = 0
        prev = text
    return text

async def run():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1400, "height": 900}, locale="ko-KR")

        await page.goto(f"{URL}/login", wait_until="networkidle", timeout=15000)
        await page.fill("#input-name", "임재필")
        await page.wait_for_selector(".ac-item", timeout=5000)
        await page.click(".ac-item:first-child")
        await page.wait_for_timeout(500)
        await page.fill("#input-password", "1234")
        await page.click("#btn-submit")
        await page.wait_for_timeout(3000)
        if "login" in page.url:
            print("로그인 실패")
            await browser.close()
            return

        await page.wait_for_load_state("networkidle", timeout=15000)
        await page.wait_for_timeout(2000)

        # Turn 1
        input_el = await page.wait_for_selector("#user-input, textarea", timeout=8000)
        await input_el.fill(Q1)
        await page.keyboard.press("Enter")
        print("Q1:", Q1)
        a1 = await wait_answer(page)
        print("A1 (앞 300자):", a1[:300].replace("\n", " "))
        print()

        # Turn 2 (참조형 + multi)
        await page.wait_for_timeout(2000)
        input_el = await page.wait_for_selector("#user-input, textarea", timeout=8000)
        await input_el.fill(Q2)
        await page.keyboard.press("Enter")
        print("Q2:", Q2)
        t0 = time.time()
        a2 = await wait_answer(page)
        elapsed = time.time() - t0
        print(f"A2 소요: {elapsed:.0f}초")
        print("=" * 60)
        print(a2[:1500])
        await browser.close()

asyncio.run(run())
