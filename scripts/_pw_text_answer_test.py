"""Playwright: 질문 전송 후 최종 텍스트 답변 출력 (스트리밍 안정화 대기)"""
import asyncio, sys
from playwright.async_api import async_playwright

URL = "http://127.0.0.1:3001"
QUESTION = sys.argv[1] if len(sys.argv) > 1 else "울 회사 제품 아무거나 1개 말해줘."

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

        input_el = await page.wait_for_selector("#user-input, textarea", timeout=8000)
        await input_el.fill(QUESTION)
        await page.keyboard.press("Enter")
        print("질문:", QUESTION)
        print("응답 대기 중...")

        # 마지막 assistant 메시지 텍스트가 6초간 변하지 않을 때까지 폴링 (최대 5분)
        prev, stable, text = "", 0, ""
        for _ in range(150):
            await page.wait_for_timeout(2000)
            text = await page.evaluate("""() => {
                const msgs = document.querySelectorAll('.message.assistant, .msg.assistant, [class*="assistant"]');
                if (!msgs.length) return '';
                return msgs[msgs.length - 1].innerText || '';
            }""")
            if text and len(text) > 80 and text == prev:
                stable += 1
                if stable >= 3:
                    break
            else:
                stable = 0
            prev = text

        print("=" * 60)
        print(text[:2000])
        await browser.close()

asyncio.run(run())
