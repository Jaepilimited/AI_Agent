"""Playwright: 로컬 3000 로그인 → 대화 이력 사이드바 확인 (임시)"""
import asyncio
from playwright.async_api import async_playwright

URL = "http://127.0.0.1:3000"

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
        await page.wait_for_timeout(4000)
        if "login" in page.url:
            print("로그인 실패")
            await browser.close()
            return
        await page.wait_for_load_state("networkidle", timeout=15000)
        await page.wait_for_timeout(3000)
        items = await page.evaluate("""() => {
            const els = document.querySelectorAll('[class*="conversation"], .convo-item, .sidebar li, [data-convo-id]');
            return els.length;
        }""")
        texts = await page.evaluate("""() => {
            const els = document.querySelectorAll('[class*="conversation"], [data-convo-id]');
            return Array.from(els).slice(0, 8).map(e => (e.innerText || '').split('\\n')[0].slice(0, 40));
        }""")
        print("사이드바 항목 수(대략):", items)
        print("상위 항목:", texts)
        await page.screenshot(path="docs/_history_restored.png", full_page=False)
        await browser.close()

asyncio.run(run())
